"""Voice corpus processor — downloads video, extracts audio, diarizes, transcribes."""
import os
import tempfile
import subprocess
import logging
import asyncio
import httpx

from config import settings
from services.r2_storage import get_r2_storage_service
import fal_client
import asyncio as _asyncio
logger = logging.getLogger(__name__)


class VoiceCorpusProcessor:
    """Process uploaded videos through ffmpeg + Pyannote + Whisper to extract speaker-isolated audio."""

    async def process_video(self, video_url: str) -> dict:
        """
        1. Download video
        2. Extract audio via ffmpeg
        3. Send to GPU worker Pyannote for diarization
        4. Identify the dominant speaker
        5. Cut out only the dominant speaker segments via ffmpeg
        6. Send speaker-isolated audio to GPU worker Whisper for transcription
        7. Return {transcript, audio_r2_key, duration_seconds}
        """
        gpu_url = settings.GPU_SERVER_URL
        if not gpu_url:
            raise RuntimeError("GPU_SERVER_URL not configured")

        r2 = get_r2_storage_service()

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Download video
            video_path = os.path.join(tmpdir, "input.mp4")
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    f.write(resp.content)

            # 2. Extract audio
            audio_path = os.path.join(tmpdir, "audio.wav")
            subprocess.run(
                ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", audio_path, "-y"],
                check=True, capture_output=True,
            )

            # 3. Upload audio to temp R2 key for GPU worker access
            temp_r2_key = f"tmp/voice_corpus/{os.urandom(8).hex()}.wav"
            await r2.upload_file(audio_path, temp_r2_key, content_type="audio/wav")
            audio_url = r2.get_public_url(temp_r2_key)

            # 4. Send to Pyannote for diarization
            # dominant_segments = []
            # try:
            #     async with httpx.AsyncClient(timeout=300) as client:
            #         diarize_resp = await client.post(
            #             f"{gpu_url}/api/pyannote-diarize",
            #             json={"audio_url": audio_url, "min_speakers": 1, "max_speakers": 5},
            #         )
            #         diarize_resp.raise_for_status()
            #         diarize_data = diarize_resp.json()

            #     dominant_speaker = diarize_data.get("dominant_speaker")
            #     speakers = diarize_data.get("speakers", [])

            #     for spk in speakers:
            #         if spk["speaker"] == dominant_speaker:
            #             dominant_segments = spk["segments"]
            #             break
            # except Exception as e:
            #     logger.warning("Diarization failed, using full audio: %s", e)

            # Fallback: use all audio if diarization failed or no segments found
            # if not dominant_segments:
                # dominant_segments = [{"start": 0.0, "end": 9999.0}]

            # 5. Cut speaker-isolated audio via ffmpeg
            # isolated_path = os.path.join(tmpdir, "isolated.wav")
            # if len(dominant_segments) == 1 and dominant_segments[0]["start"] == 0.0 and dominant_segments[0]["end"] >= 9999.0:
            #     # No diarization — just copy audio as-is
            #     import shutil
            #     shutil.copy(audio_path, isolated_path)
            # else:
            #     filter_parts = []
            #     for i, seg in enumerate(dominant_segments):
            #         filter_parts.append(
            #             f"[0:a]atrim=start={seg['start']}:end={seg['end']},asetpts=PTS-STARTPTS[a{i}]"
            #         )
            #     concat_inputs = "".join(f"[a{i}]" for i in range(len(dominant_segments)))
            #     filter_graph = (
            #         ";".join(filter_parts)
            #         + f";{concat_inputs}concat=n={len(dominant_segments)}:v=0:a=1[out]"
            #     )
            #     subprocess.run(
            #         ["ffmpeg", "-i", audio_path, "-filter_complex", filter_graph,
            #          "-map", "[out]", isolated_path, "-y"],
            #         check=True, capture_output=True,
            #     )

            # Upload isolated audio for Whisper
            # isolated_r2_key = f"tmp/voice_corpus/{os.urandom(8).hex()}_isolated.wav"
            # await r2.upload_file(isolated_path, isolated_r2_key, content_type="audio/wav")
            # isolated_url = r2.get_public_url(isolated_r2_key)

            # 6. Send to Whisper for transcription
            # transcript = ""
            # duration = 0.0
            # try:
            #     async with httpx.AsyncClient(timeout=300) as client:
            #         whisper_resp = await client.post(
            #             f"{gpu_url}/api/whisper-transcribe",
            #             json={"audio_url": isolated_url, "language": "auto"},
            #         )
            #         whisper_resp.raise_for_status()
            #         whisper_data = whisper_resp.json()
            #     transcript = whisper_data.get("transcript", "")
            #     duration = whisper_data.get("duration_seconds", 0.0)
            # except Exception as e:
            #     logger.warning("Whisper transcription failed: %s", e)

            # Probe actual duration from isolated audio if whisper didn't return it
            # 4. Send to fal.ai Whisper — transcription + diarization in one call

            def _run_fal():
                return fal_client.subscribe(
                    "fal-ai/whisper",
                    arguments={
                        "audio_url": audio_url,
                        "task": "transcribe",
                        "chunk_level": "segment",
                        "diarize": True,
                    },
                )

            transcript = ""
            duration = 0.0
            dominant_segments = []
            try:
                result = await _asyncio.to_thread(_run_fal)
                transcript = result.get("text", "")
                diar_segments = result.get("diarization_segments", [])

                # Tally airtime per speaker to find the dominant one
                speaker_durations: dict[str, float] = {}
                speaker_segs: dict[str, list] = {}
                for seg in diar_segments:
                    spk = seg.get("speaker")
                    start = float(seg.get("timestamp", [0, 0])[0])
                    end = float(seg.get("timestamp", [0, 0])[1])
                    speaker_durations[spk] = speaker_durations.get(spk, 0.0) + (end - start)
                    speaker_segs.setdefault(spk, []).append({"start": start, "end": end})

                if speaker_durations:
                    dominant = max(speaker_durations, key=speaker_durations.get)
                    dominant_segments = speaker_segs[dominant]
            except Exception as e:
                logger.warning("fal.ai Whisper call failed: %s", e)

            if not dominant_segments:
                dominant_segments = [{"start": 0.0, "end": 9999.0}]

            # 5. Cut speaker-isolated audio via ffmpeg (unchanged logic)
            isolated_path = os.path.join(tmpdir, "isolated.wav")
            if len(dominant_segments) == 1 and dominant_segments[0]["start"] == 0.0 and dominant_segments[0]["end"] >= 9999.0:
                import shutil
                shutil.copy(audio_path, isolated_path)
            else:
                filter_parts = []
                for i, seg in enumerate(dominant_segments):
                    filter_parts.append(
                        f"[0:a]atrim=start={seg['start']}:end={seg['end']},asetpts=PTS-STARTPTS[a{i}]"
                    )
                concat_inputs = "".join(f"[a{i}]" for i in range(len(dominant_segments)))
                filter_graph = (
                    ";".join(filter_parts)
                    + f";{concat_inputs}concat=n={len(dominant_segments)}:v=0:a=1[out]"
                )
                subprocess.run(
                    ["ffmpeg", "-i", audio_path, "-filter_complex", filter_graph,
                     "-map", "[out]", isolated_path, "-y"],
                    check=True, capture_output=True,
                )

            isolated_r2_key = f"tmp/voice_corpus/{os.urandom(8).hex()}_isolated.wav"
            await r2.upload_file(isolated_path, isolated_r2_key, content_type="audio/wav")

            if not duration:
                try:
                    import json as _json
                    probe_result = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", isolated_path],
                        capture_output=True, text=True, timeout=30,
                    )
                    probe_data = _json.loads(probe_result.stdout)
                    duration = float(probe_data.get("format", {}).get("duration", 0) or 0)
                except Exception:
                    pass
            
            if not duration:
                try:
                    import json as _json
                    probe_result = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json",
                         "-show_format", isolated_path],
                        capture_output=True, text=True, timeout=30,
                    )
                    probe_data = _json.loads(probe_result.stdout)
                    duration = float(probe_data.get("format", {}).get("duration", 0) or 0)
                except Exception:
                    pass

            # Clean up temp R2 key
            try:
                loop_cleanup_key = temp_r2_key
                import asyncio
                # Fire and forget — don't block on cleanup failure
                async def _delete():
                    try:
                        import boto3
                        from botocore.config import Config as BotoConfig
                        s3 = boto3.client(
                            "s3",
                            endpoint_url=settings.R2_ENDPOINT,
                            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                            config=BotoConfig(signature_version="s3v4"),
                            region_name="auto",
                        )
                        s3.delete_object(Bucket=settings.R2_BUCKET, Key=loop_cleanup_key)
                    except Exception:
                        pass
                await _delete()
            except Exception:
                pass

            return {
                "transcript": transcript,
                "audio_r2_key": isolated_r2_key,
                "duration_seconds": duration,
            }
