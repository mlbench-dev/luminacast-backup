"""Celery task to process a VoiceCorpusEntry (diarize + transcribe)."""
import asyncio
import logging
import shutil
import tempfile

import sentry_sdk
from tasks import celery_app

logger = logging.getLogger(__name__)


def _make_session_factory():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(name="tasks.voice_corpus.process", bind=True, max_retries=1)
def process_voice_corpus_entry(self, entry_id: str):
    """Process a VoiceCorpusEntry through diarization and Whisper transcription."""
    asyncio.run(_process_async(entry_id))


async def _process_async(entry_id: str):
    from models.voice_corpus import VoiceCorpusEntry
    from services.voice_corpus_processor import VoiceCorpusProcessor
    from services.r2_storage import get_r2_storage_service

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    async with factory() as session:
        entry = await session.get(VoiceCorpusEntry, entry_id)
        if not entry:
            logger.error("VoiceCorpusEntry %s not found", entry_id)
            return

        try:
            entry.status = "processing"
            await session.commit()

            # Determine the video/audio URL to process
            if entry.source_type == "uploaded" and entry.audio_r2_key:
                video_url = r2.get_public_url(entry.audio_r2_key)
            elif entry.source_url:
                video_url = entry.source_url
            else:
                raise ValueError("No video source available for entry")

            processor = VoiceCorpusProcessor()
            result = await processor.process_video(video_url)

            entry.transcript = result["transcript"]
            entry.audio_r2_key = result["audio_r2_key"]
            entry.duration_seconds = result["duration_seconds"]
            entry.status = "ready"
            await session.commit()

            logger.info("VoiceCorpusEntry %s processed successfully", entry_id)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Voice corpus processing failed for %s: %s", entry_id, e)
            entry.status = "failed"
            entry.error_message = str(e)[:500]
            await session.commit()
