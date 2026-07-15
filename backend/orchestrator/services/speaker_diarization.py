"""
Speaker diarization service using Pyannote 3.1 on VPS CPU.
Identifies dominant speaker segments in audio files.
"""
import logging
import os
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Lazy-load the pipeline to avoid slow import at module level
_pipeline = None


def _get_pipeline():
    """Lazy-load Pyannote speaker diarization pipeline."""
    global _pipeline
    if _pipeline is None:
        from pyannote.audio import Pipeline
        from config import settings

        hf_token = settings.HF_TOKEN
        if not hf_token:
            raise ValueError("HF_TOKEN is required for Pyannote speaker diarization")

        logger.info("Loading Pyannote speaker-diarization-3.1 pipeline (CPU)...")
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token,
        )
        # Force CPU inference
        import torch
        _pipeline.to(torch.device("cpu"))
        logger.info("Pyannote pipeline loaded successfully")
    return _pipeline


def extract_primary_speaker_segments(
    audio_path: str,
    min_segment_duration: float = 2.0,
) -> List[Tuple[float, float]]:
    """
    Run speaker diarization and return time segments for the dominant speaker.

    Args:
        audio_path: Path to WAV audio file (mono, 44.1kHz recommended)
        min_segment_duration: Minimum segment length in seconds (default 2.0)

    Returns:
        List of (start_seconds, end_seconds) tuples for the dominant speaker,
        sorted chronologically. Returns empty list if diarization fails.
    """
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found: {audio_path}")
        return []

    try:
        pipeline = _get_pipeline()

        logger.info(f"Running speaker diarization on {audio_path}...")
        diarization = pipeline(audio_path)

        # Tally total airtime per speaker
        speaker_durations: dict[str, float] = {}
        speaker_segments: dict[str, List[Tuple[float, float]]] = {}

        # Newer pyannote returns DiarizeOutput; itertracks is on .speaker_diarization
        annotation = getattr(diarization, "speaker_diarization", diarization)
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            duration = turn.end - turn.start
            if duration < min_segment_duration:
                continue

            speaker_durations[speaker] = speaker_durations.get(speaker, 0.0) + duration
            if speaker not in speaker_segments:
                speaker_segments[speaker] = []
            speaker_segments[speaker].append((turn.start, turn.end))

        if not speaker_durations:
            logger.warning("No speakers found with segments >= %.1fs", min_segment_duration)
            return []

        # Find dominant speaker (most total airtime)
        dominant_speaker = max(speaker_durations, key=speaker_durations.get)
        dominant_duration = speaker_durations[dominant_speaker]
        total_speakers = len(speaker_durations)

        logger.info(
            f"Diarization complete: {total_speakers} speakers found. "
            f"Dominant speaker '{dominant_speaker}' has {dominant_duration:.1f}s of audio "
            f"across {len(speaker_segments[dominant_speaker])} segments"
        )

        # Return dominant speaker's segments sorted by start time
        segments = sorted(speaker_segments[dominant_speaker], key=lambda s: s[0])
        return segments

    except Exception as e:
        logger.error(f"Speaker diarization failed: {e}", exc_info=True)
        return []
