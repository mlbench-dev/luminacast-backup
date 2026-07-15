"""Celery tasks for music generation and LoRA training."""

import asyncio
import logging
from datetime import datetime

from tasks import celery_app
from config import settings

import sentry_sdk
logger = logging.getLogger(__name__)


def _make_session():
    """Create a fresh sync-compatible async session for Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    engine = create_engine(settings.sync_database_url)
    return sessionmaker(bind=engine)()


@celery_app.task(name="music.generate_track", bind=True, max_retries=2)
def generate_track_task(self, track_id: str):
    """Run music generation for a single track."""
    from models.sound_cast import MusicTrack, MusicTrackStatus, SoundCast, SoundCastStatus
    from services.ace_step_client import get_ace_step_client

    db = _make_session()
    try:
        track = db.get(MusicTrack, track_id)
        if not track:
            logger.error("Track %s not found", track_id)
            return

        track.status = MusicTrackStatus.GENERATING
        db.commit()

        # Resolve LoRA from sound cast
        sc = db.get(SoundCast, track.sound_cast_id)
        lora_r2_key = sc.lora_r2_key if sc and sc.status == SoundCastStatus.TRAINED else ""

        output_key = f"music/{track.user_id}/{track.sound_cast_id}/{track.id}.wav"

        client = get_ace_step_client()
        result = asyncio.run(client.generate_music(
            prompt=track.prompt,
            lyrics=track.lyrics,
            duration_seconds=track.duration_seconds,
            output_r2_key=output_key,
            lora_r2_key=lora_r2_key or None,
            seed=track.seed,
            guidance_scale=track.guidance_scale,
            inference_steps=track.inference_steps,
            scheduler_type=track.scheduler_type,
        ))

        track.status = MusicTrackStatus.READY
        track.audio_r2_key = result["r2_key"]
        track.actual_duration_seconds = result["duration_seconds"]
        track.generation_time_seconds = result["generation_time_seconds"]
        db.commit()
        logger.info("Track %s generated in %.1fs", track_id, result["generation_time_seconds"])

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Track generation failed: %s", e)
        db.rollback()
        track = db.get(MusicTrack, track_id)
        if track:
            track.status = MusicTrackStatus.FAILED
            track.generation_error = str(e)[:500]
            db.commit()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()


@celery_app.task(name="music.train_sound_cast", bind=True, max_retries=1)
def train_sound_cast_task(self, sound_cast_id: str):
    """Run LoRA training for a sound cast."""
    from models.sound_cast import SoundCast, SoundCastStatus
    from services.ace_step_client import get_ace_step_client

    db = _make_session()
    try:
        sc = db.get(SoundCast, sound_cast_id)
        if not sc:
            logger.error("SoundCast %s not found", sound_cast_id)
            return

        sc.status = SoundCastStatus.TRAINING_IN_PROGRESS
        sc.training_started_at = datetime.utcnow()
        db.commit()

        lora_output_key = f"music-loras/{sc.user_id}/{sound_cast_id}.safetensors"

        client = get_ace_step_client()
        try:
            result = asyncio.run(client.train_lora(
                sound_cast_id=sound_cast_id,
                training_audio_r2_keys=sc.training_audio_keys,
                training_prompts=sc.training_prompts,
                output_r2_key=lora_output_key,
                steps=sc.training_steps,
            ))
        except Exception as gpu_err:
            sentry_sdk.capture_exception(gpu_err)
            err_str = str(gpu_err)
            logger.error(f"LoRA training failed: {gpu_err}")
            sc.status = SoundCastStatus.FAILED
            if "404" in err_str or "Connect" in err_str:
                sc.training_error = "GPU music training is being configured. Please try again in a few hours."
            else:
                sc.training_error = f"Training error: {err_str[:2000]}"
            db.commit()
            return

        sc.status = SoundCastStatus.TRAINED
        sc.lora_r2_key = result.get("output_r2_key") or result.get("lora_r2_key", "")
        sc.training_loss = result.get("final_loss") or result.get("loss", 0.0)
        sc.training_completed_at = datetime.utcnow()
        db.commit()
        logger.info("SoundCast %s trained in %.0fs, loss=%.4f",
                     sound_cast_id,
                     result.get("elapsed_seconds") or result.get("training_time_seconds", 0),
                     sc.training_loss)

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("LoRA training failed: %s", e)
        db.rollback()
        sc = db.get(SoundCast, sound_cast_id)
        if sc:
            sc.status = SoundCastStatus.FAILED
            # Detect 404 / connection errors to the GPU server (ACE-Step not installed yet)
            err_str = str(e)
            if "404" in err_str or "Not Found" in err_str or "ConnectError" in err_str or "ConnectionRefused" in err_str:
                sc.training_error = (
                    "GPU music training is not yet available on this server. "
                    "We are setting it up. Please try again in a few hours."
                )
            else:
                sc.training_error = err_str[:500]
            db.commit()
        # Don't retry if endpoint doesn't exist — it won't appear in 60s
        if "404" in str(e) or "ConnectError" in str(e) or "ConnectionRefused" in str(e):
            return
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()
