"""Unified GPU Worker — FastAPI server for BS-RoFormer, Fish Speech, InfiniteTalk, Upscale.

Runs on a dedicated HOSTKEY RTX 4090 server. Models hot-swap behind asyncio.Lock()
so only one model uses the GPU at a time. Models stay loaded between same-type jobs.

Endpoints:
  POST /api/bs-roformer   — vocal isolation + Whisper transcription
  POST /api/fish-speech    — TTS generation (stub — TODO)
  POST /api/infinitetalk   — talking head video generation
  POST /api/upscale-video  — video upscaling via Real-ESRGAN
  GET  /api/health         — GPU status
"""

import asyncio
import gc
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from typing import Optional

import boto3
import numpy as np
import requests
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from worker_ffmpeg_compose import _run_ffmpeg_compose as _run_ffmpeg_compose_v2

# ── Sentry error tracking ──
import sentry_sdk
_sentry_dsn = os.environ.get("SENTRY_GPU_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.2, environment="production")
    print("Sentry initialized for GPU worker")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gpu-worker")

# Better Stack logging
try:
    from logtail import LogtailHandler
    betterstack_token = os.environ.get("BETTERSTACK_GPU_TOKEN") or os.environ.get("BETTERSTACK_TOKEN")
    if betterstack_token:
        handler = LogtailHandler(source_token=betterstack_token)
        logging.getLogger().addHandler(handler)
        logger.info("Better Stack logging connected")
except ImportError:
    pass  # logtail not installed

app = FastAPI(title="Luminacast GPU Worker")

# ── GPU state ──
gpu_lock = asyncio.Lock()
current_model: Optional[str] = None
model_instance = None

# ── R2 config ──
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "luminacast")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "https://media.luminacast.com")

# ── InfiniteTalk paths ──
INFINITETALK_REPO = "/opt/gpu-worker/InfiniteTalk"
MODELS_DIR = "/opt/gpu-worker/models"
WAN_CKPT_DIR = os.path.join(MODELS_DIR, "Wan2.1-I2V-14B-480P")
WAV2VEC_DIR = os.path.join(MODELS_DIR, "chinese-wav2vec2-base")
INFINITETALK_WEIGHTS = os.path.join(MODELS_DIR, "infinitetalk/single/infinitetalk.safetensors")


def get_s3():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        region_name="auto",
    )


# ═══════════════════════════════════════════
# Model management
# ═══════════════════════════════════════════

def _unload_current():
    global current_model, model_instance
    if model_instance is not None:
        logger.info(f"Unloading model: {current_model}")
        del model_instance
        model_instance = None
        current_model = None
        torch.cuda.empty_cache()
        gc.collect()


def _load_bs_roformer():
    global current_model, model_instance
    if current_model == "bs_roformer":
        return model_instance
    _unload_current()
    logger.info("Loading BS-RoFormer model...")
    from audio_separator.separator import Separator
    sep = Separator()
    sep.load_model("model_bs_roformer_ep_317_sdr_12.9755.ckpt")
    model_instance = sep
    current_model = "bs_roformer"
    logger.info("BS-RoFormer loaded")
    return sep


def _load_whisper():
    """Load Whisper large-v3 (doesn't conflict with BS-RoFormer — both fit in 24GB)."""
    import whisper
    return whisper.load_model("large-v3", device="cuda")


# Cache whisper model separately (it's small enough to keep alongside BS-RoFormer)
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        logger.info("Loading Whisper large-v3...")
        import whisper
        _whisper_model = whisper.load_model("large-v3", device="cuda")
        logger.info("Whisper loaded")
    return _whisper_model



def _load_realesrgan(enhance_faces: bool = False):
    """Load Real-ESRGAN 4x upscaler (+ optional GFPGAN face enhancer).

    Follows hot-swap pattern: check current_model, call _unload_current if different.
    Uses tile=400 and half=True for VRAM efficiency on RTX 4090.
    """
    global current_model, model_instance
    target = "realesrgan_face" if enhance_faces else "realesrgan"
    if current_model == target:
        return model_instance
    _unload_current()
    logger.info(f"Loading Real-ESRGAN (enhance_faces={enhance_faces})...")

    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    rrdb_model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                         num_block=23, num_grow_ch=32, scale=4)
    upsampler = RealESRGANer(
        scale=4,
        model_path="/opt/gpu-worker/models/RealESRGAN_x4plus.pth",
        model=rrdb_model,
        tile=400,
        tile_pad=10,
        pre_pad=0,
        half=True,
        device="cuda",
    )

    face_enhancer = None
    if enhance_faces:
        from gfpgan import GFPGANer
        face_enhancer = GFPGANer(
            model_path="/opt/gpu-worker/models/GFPGANv1.4.pth",
            upscale=4,
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=upsampler,
        )

    model_instance = {"upsampler": upsampler, "face_enhancer": face_enhancer}
    current_model = target
    logger.info(f"Real-ESRGAN loaded (target={target})")
    return model_instance


# ═══════════════════════════════════════════
# BS-RoFormer pipeline
# ═══════════════════════════════════════════

class BSRoFormerRequest(BaseModel):
    audio_url: str
    output_key: str
    max_duration: int = 90


def _download_audio(url: str, work_dir: str) -> str:
    """Download audio/video from URL, extract WAV."""
    audio_path = os.path.join(work_dir, "input.wav")

    # Strip query params for extension check
    url_path = url.split("?")[0]
    if url_path.endswith(".wav") or url_path.endswith(".mp3"):
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        ext = ".wav" if url_path.endswith(".wav") else ".mp3"
        raw_path = os.path.join(work_dir, "raw_input" + ext)
        with open(raw_path, "wb") as f:
            f.write(resp.content)
        # If already WAV, use directly
        if ext == ".wav":
            return raw_path
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", audio_path],
            capture_output=True, timeout=30,
        )
        return audio_path
    else:
        # Download video/generic and extract audio
        raw_path = os.path.join(work_dir, "raw_input.mp4")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        with open(raw_path, "wb") as f:
            f.write(resp.content)
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", audio_path],
            capture_output=True, timeout=30,
        )
        return audio_path


def _isolate_vocals(separator, audio_path: str, work_dir: str) -> str:
    """Run BS-RoFormer vocal isolation on GPU."""
    output_dir = os.path.join(work_dir, "separated")
    os.makedirs(output_dir, exist_ok=True)

    separator.output_dir = output_dir
    output_files = separator.separate(audio_path)

    # separator.separate() returns relative filenames — resolve to absolute
    vocals_path = None
    for f in output_files:
        # Try absolute first, then resolve relative to output_dir
        abs_path = f if os.path.isabs(f) else os.path.join(output_dir, os.path.basename(f))
        if not os.path.exists(abs_path):
            abs_path = os.path.join(output_dir, f)
        if os.path.exists(abs_path) and ("vocal" in f.lower() or "voice" in f.lower()):
            vocals_path = abs_path
            break
    # Fallback: find any file with "Vocal" in the output dir
    if not vocals_path:
        for fname in os.listdir(output_dir):
            if "vocal" in fname.lower():
                vocals_path = os.path.join(output_dir, fname)
                break
    if not vocals_path:
        logger.warning("BS-RoFormer produced no vocals, using original")
        return audio_path

    logger.info(f"Vocals isolated: {vocals_path} ({os.path.getsize(vocals_path)} bytes)")
    return vocals_path


def _process_audio(vocals_path: str, work_dir: str, max_duration: int = 90) -> str:
    """Silence trim, highpass, loudnorm, select best segment."""
    processed_path = os.path.join(work_dir, "processed.wav")
    ffmpeg_filter = (
        "highpass=f=80,"
        "lowpass=f=14000,"
        "silenceremove=start_periods=1:start_silence=0.3:start_threshold=-35dB:"
        "stop_periods=-1:stop_silence=0.3:stop_threshold=-35dB,"
        "loudnorm=I=-16:LRA=11:TP=-1.5"
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", vocals_path, "-af", ffmpeg_filter,
         "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", processed_path],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0 or not os.path.exists(processed_path):
        return vocals_path

    file_size = os.path.getsize(processed_path)
    duration_s = file_size / (44100 * 2)

    if duration_s <= max_duration:
        return processed_path

    # Select densest speech segment
    final_path = os.path.join(work_dir, "final.wav")
    try:
        with wave.open(processed_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32)

        sample_rate = 44100
        window_size = sample_rate
        n_windows = len(audio_data) // window_size
        energies = []
        for i in range(n_windows):
            chunk = audio_data[i * window_size:(i + 1) * window_size]
            energies.append(np.sqrt(np.mean(chunk ** 2)))

        target_windows = min(max_duration, n_windows)
        best_start = 0
        best_energy = 0
        for start in range(n_windows - target_windows + 1):
            total_energy = sum(energies[start:start + target_windows])
            if total_energy > best_energy:
                best_energy = total_energy
                best_start = start

        subprocess.run(
            ["ffmpeg", "-y", "-i", processed_path,
             "-ss", str(best_start), "-t", str(target_windows),
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", final_path],
            capture_output=True, timeout=30,
        )
        if os.path.exists(final_path) and os.path.getsize(final_path) > 1000:
            return final_path
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Segment selection failed: {e}")

    return processed_path


def _transcribe(whisper_model, audio_path: str) -> str:
    """Transcribe with Whisper large-v3 on GPU."""
    try:
        result = whisper_model.transcribe(audio_path, language="en")
        text = result.get("text", "").strip()
        logger.info(f"Transcription ({len(text)} chars)")
        return text
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Transcription failed: {e}")
        return ""


def _run_bs_roformer_pipeline(req: BSRoFormerRequest) -> dict:
    """Full BS-RoFormer pipeline (runs in executor thread)."""
    work_dir = tempfile.mkdtemp(prefix="gpu_bsrf_")
    try:
        # 1. Download
        logger.info(f"Downloading: {req.audio_url[:80]}")
        audio_path = _download_audio(req.audio_url, work_dir)
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            raise RuntimeError("Failed to download audio")

        # 2. Vocal isolation
        separator = _load_bs_roformer()
        vocals_path = _isolate_vocals(separator, audio_path, work_dir)

        # 3. Process
        final_path = _process_audio(vocals_path, work_dir, req.max_duration)
        final_size = os.path.getsize(final_path)
        duration = final_size / (44100 * 2)

        # 4. Transcribe
        whisper_model = _get_whisper()
        transcript = _transcribe(whisper_model, final_path)

        # 5. Upload to R2
        s3 = get_s3()
        s3.upload_file(final_path, R2_BUCKET, req.output_key,
                       ExtraArgs={"ContentType": "audio/wav"})
        vocals_url = f"{R2_PUBLIC_URL}/{req.output_key}"

        return {
            "vocals_url": vocals_url,
            "transcript": transcript,
            "duration_seconds": round(duration, 1),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/api/bs-roformer")
async def bs_roformer_endpoint(req: BSRoFormerRequest):
    async with gpu_lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: _run_bs_roformer_pipeline(req))
        return result


# ═══════════════════════════════════════════
# Fish Speech (stub — TODO)
# ═══════════════════════════════════════════

class FishSpeechRequest(BaseModel):
    text: str
    reference_audio_b64: str
    format: str = "mp3"


@app.post("/api/fish-speech")
async def fish_speech_endpoint(req: FishSpeechRequest):
    raise HTTPException(501, "Fish Speech not yet implemented on GPU server. Use RunPod fallback.")


# ═══════════════════════════════════════════
# InfiniteTalk — talking head video generation
# ═══════════════════════════════════════════

class InfiniteTalkRequest(BaseModel):
    image_url: str
    audio_url: str
    output_key: str = ""
    prompt: str = "A person is talking naturally."
    size: str = "480p"
    sample_steps: int = 40
    max_seconds: int = 40  # max video duration in seconds (default 40s = 1000 frames)


# Cached InfiniteTalk pipeline and wav2vec components
_infinitetalk_pipeline = None
_wav2vec_extractor = None
_wav2vec_encoder = None


def _load_infinitetalk():
    """Load InfiniteTalk pipeline with VRAM management for RTX 4090 (24GB).

    Uses num_persistent_param_in_dit=0 for full CPU offloading of the 14B DiT model.
    This means inference is slower but fits in 24GB VRAM.
    """
    global current_model, model_instance, _infinitetalk_pipeline, _wav2vec_extractor, _wav2vec_encoder

    if current_model == "infinitetalk" and _infinitetalk_pipeline is not None:
        return _infinitetalk_pipeline, _wav2vec_extractor, _wav2vec_encoder

    # Unload any other model first
    _unload_current()

    # Also clear whisper from VRAM if loaded
    global _whisper_model
    if _whisper_model is not None:
        logger.info("Unloading Whisper to free VRAM for InfiniteTalk")
        del _whisper_model
        _whisper_model = None
        torch.cuda.empty_cache()
        gc.collect()

    logger.info("Loading InfiniteTalk pipeline...")

    # Add InfiniteTalk repo to path
    if INFINITETALK_REPO not in sys.path:
        sys.path.insert(0, INFINITETALK_REPO)

    import wan
    from wan.configs import WAN_CONFIGS
    from transformers import Wav2Vec2FeatureExtractor
    from src.audio_analysis.wav2vec2 import Wav2Vec2Model

    cfg = WAN_CONFIGS["infinitetalk-14B"]

    logger.info("Creating InfiniteTalkPipeline (this will take a few minutes)...")
    pipeline = wan.InfiniteTalkPipeline(
        config=cfg,
        checkpoint_dir=WAN_CKPT_DIR,
        device_id=0,
        rank=0,
        t5_fsdp=False,
        dit_fsdp=False,
        use_usp=False,
        t5_cpu=False,
        infinitetalk_dir=INFINITETALK_WEIGHTS,
    )

    # Enable VRAM management — offload DiT params to CPU, keep 0 persistent in VRAM
    # This is necessary for RTX 4090 (24GB) with a 14B model (~28GB in bf16)
    pipeline.vram_management = True
    pipeline.enable_vram_management(num_persistent_param_in_dit=0)

    logger.info("InfiniteTalk pipeline loaded with VRAM management")

    # Load wav2vec2 components
    logger.info("Loading wav2vec2 audio encoder...")
    # Use attn_implementation="eager" to avoid SDPA vs output_attentions conflict
    # (InfiniteTalk's custom Wav2Vec2Model sets config.output_attentions=True in forward())
    wav2vec_encoder = Wav2Vec2Model.from_pretrained(
        WAV2VEC_DIR, local_files_only=True, attn_implementation="eager"
    ).to("cpu")
    wav2vec_encoder.feature_extractor._freeze_parameters()
    wav2vec_extractor = Wav2Vec2FeatureExtractor.from_pretrained(WAV2VEC_DIR, local_files_only=True)
    logger.info("wav2vec2 loaded")

    _infinitetalk_pipeline = pipeline
    _wav2vec_extractor = wav2vec_extractor
    _wav2vec_encoder = wav2vec_encoder
    model_instance = pipeline
    current_model = "infinitetalk"

    return pipeline, wav2vec_extractor, wav2vec_encoder


def _get_audio_embedding(speech_array, wav2vec_extractor, wav2vec_encoder, sr=16000):
    """Extract wav2vec2 audio embeddings for InfiniteTalk."""
    from einops import rearrange

    audio_duration = len(speech_array) / sr
    video_length = audio_duration * 25  # 25 fps

    audio_feature = np.squeeze(
        wav2vec_extractor(speech_array, sampling_rate=sr).input_values
    )
    audio_feature = torch.from_numpy(audio_feature).float().to(device="cpu")
    audio_feature = audio_feature.unsqueeze(0)

    with torch.no_grad():
        embeddings = wav2vec_encoder(audio_feature, seq_len=int(video_length), output_hidden_states=True)

    if len(embeddings) == 0:
        raise RuntimeError("Failed to extract audio embedding")

    audio_emb = torch.stack(embeddings.hidden_states[1:], dim=1).squeeze(0)
    audio_emb = rearrange(audio_emb, "b s d -> s b d")
    audio_emb = audio_emb.cpu().detach()
    return audio_emb


def _run_infinitetalk_pipeline(req: InfiniteTalkRequest) -> dict:
    """Full InfiniteTalk pipeline (runs in executor thread)."""
    import librosa
    import pyloudnorm as pyln
    import soundfile as sf
    from einops import rearrange

    work_dir = tempfile.mkdtemp(prefix="gpu_italk_")
    try:
        # 1. Download image
        logger.info(f"Downloading image: {req.image_url[:80]}")
        img_resp = requests.get(req.image_url, timeout=120)
        img_resp.raise_for_status()
        img_path = os.path.join(work_dir, "input_image.png")
        with open(img_path, "wb") as f:
            f.write(img_resp.content)

        # 2. Download audio
        logger.info(f"Downloading audio: {req.audio_url[:80]}")
        audio_resp = requests.get(req.audio_url, timeout=120)
        audio_resp.raise_for_status()
        url_path = req.audio_url.split("?")[0]
        ext = ".wav"
        for e in [".mp3", ".mp4", ".m4a", ".ogg", ".flac"]:
            if url_path.endswith(e):
                ext = e
                break
        raw_audio_path = os.path.join(work_dir, f"raw_audio{ext}")
        with open(raw_audio_path, "wb") as f:
            f.write(audio_resp.content)

        # Convert to WAV 16kHz mono if needed
        audio_16k_path = os.path.join(work_dir, "audio_16k.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_audio_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", audio_16k_path],
            capture_output=True, timeout=60,
        )
        if not os.path.exists(audio_16k_path) or os.path.getsize(audio_16k_path) < 100:
            raise RuntimeError("Failed to convert audio to 16kHz WAV")

        # 3. Load and normalize audio
        speech_array, sr = librosa.load(audio_16k_path, sr=16000)
        meter = pyln.Meter(sr)
        loudness = meter.integrated_loudness(speech_array)
        if abs(loudness) <= 100:
            speech_array = pyln.normalize.loudness(speech_array, loudness, -23)

        # Save normalized audio for video muxing
        sum_audio_path = os.path.join(work_dir, "sum_all.wav")
        sf.write(sum_audio_path, speech_array, 16000)

        audio_duration = len(speech_array) / 16000
        logger.info(f"Audio duration: {audio_duration:.1f}s")

        # 4. Load InfiniteTalk pipeline
        pipeline, wav2vec_extractor, wav2vec_encoder = _load_infinitetalk()

        # 5. Get audio embeddings
        logger.info("Extracting audio embeddings...")
        audio_emb = _get_audio_embedding(speech_array, wav2vec_extractor, wav2vec_encoder, sr=16000)
        emb_path = os.path.join(work_dir, "audio_emb.pt")
        torch.save(audio_emb, emb_path)
        v_length = audio_emb.shape[0]

        # 6. Prepare input dict
        max_frames = min(req.max_seconds * 25, int(audio_duration * 25) + 25)  # match audio + 1s buffer
        input_clip = {
            "prompt": req.prompt,
            "cond_video": img_path,
            "cond_audio": {"person1": emb_path},
            "video_audio": sum_audio_path,
        }

        size_key = "infinitetalk-480" if req.size == "480p" else "infinitetalk-720"

        # 7. Generate video
        logger.info(f"Generating video ({max_frames} frames, {req.sample_steps} steps)...")

        # Create a mock args object for InfiniteTalk's extra_args parameter
        from easydict import EasyDict
        mock_args = EasyDict(
            use_teacache=False,
            use_apg=False,
            teacache_thresh=0.2,
            apg_momentum=0.1,
            apg_norm_threshold=15.0,
            size=size_key,
            mode="streaming",
            audio_mode="localfile",
            scene_seg=False,
        )

        video_tensor = pipeline.generate_infinitetalk(
            input_clip,
            size_buckget=size_key,
            motion_frame=9,
            frame_num=81,
            shift=7 if req.size == "480p" else 11,
            sampling_steps=req.sample_steps,
            text_guide_scale=5.0,
            audio_guide_scale=4.0,
            seed=random.randint(0, 99999999),
            offload_model=None,
            max_frames_num=max_frames,
            color_correction_strength=0.0,
            extra_args=mock_args,
        )
        logger.info(f"Video generated: tensor shape {video_tensor.shape}")

        # 8. Save video with audio
        save_prefix = os.path.join(work_dir, "output")

        # Import save utility from InfiniteTalk
        from wan.utils.multitalk_utils import save_video_ffmpeg
        save_video_ffmpeg(video_tensor, save_prefix, [sum_audio_path], fps=25, quality=5, high_quality_save=False)

        output_video_path = save_prefix + ".mp4"
        if not os.path.exists(output_video_path):
            raise RuntimeError("Video save failed — output file not found")

        video_size = os.path.getsize(output_video_path)
        logger.info(f"Output video: {video_size / 1024 / 1024:.1f} MB")

        # 9. Upload to R2
        output_key = req.output_key
        if not output_key:
            import hashlib
            h = hashlib.sha256(f"{req.image_url}_{req.audio_url}".encode()).hexdigest()[:16]
            output_key = f"infinitetalk/{h}/output.mp4"

        s3 = get_s3()
        s3.upload_file(output_video_path, R2_BUCKET, output_key,
                       ExtraArgs={"ContentType": "video/mp4"})
        video_url = f"{R2_PUBLIC_URL}/{output_key}"

        return {
            "video_url": video_url,
            "duration_seconds": round(audio_duration, 1),
            "frames": video_tensor.shape[1] if len(video_tensor.shape) > 1 else 0,
            "size": req.size,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# Need random for seed generation
import random


@app.post("/api/infinitetalk")
async def infinitetalk_endpoint(req: InfiniteTalkRequest):
    # Check if models are downloaded
    if not os.path.exists(INFINITETALK_WEIGHTS):
        raise HTTPException(503, "InfiniteTalk weights not downloaded yet")
    if not os.path.exists(os.path.join(WAN_CKPT_DIR, "diffusion_pytorch_model-00001-of-00007.safetensors")):
        raise HTTPException(503, "Wan2.1 base model not downloaded yet")
    if not os.path.exists(os.path.join(WAV2VEC_DIR, "model.safetensors")):
        raise HTTPException(503, "wav2vec2 model not downloaded yet")

    async with gpu_lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: _run_infinitetalk_pipeline(req))
        return result


# ═══════════════════════════════════════════
# Video Upscale — Real-ESRGAN (Python) + GFPGAN face enhancement
# ═══════════════════════════════════════════


class UpscaleVideoRequest(BaseModel):
    video_url: str
    output_key: str
    target_height: int = 1080  # 720 or 1080
    enhance_faces: bool = False


def _get_video_info(video_path: str) -> dict:
    """Probe a video file for fps, resolution, duration using ffprobe."""
    import json as _json
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", video_path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    probe = _json.loads(result.stdout)
    video_stream = None
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            video_stream = s
            break
    if not video_stream:
        raise RuntimeError("No video stream found in file")

    width = int(video_stream["width"])
    height = int(video_stream["height"])

    fps_str = video_stream.get("r_frame_rate", "25/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den)
    else:
        fps = float(fps_str)

    has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
    duration = float(probe.get("format", {}).get("duration", 0))

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": has_audio,
        "duration": round(duration, 2),
    }


def _run_upscale_pipeline(req: UpscaleVideoRequest) -> dict:
    """Full Real-ESRGAN video upscale pipeline (runs in executor thread)."""
    import cv2

    work_dir = tempfile.mkdtemp(prefix="gpu_upscale_")
    t_start = time.time()
    try:
        # 1. Download source video
        _update_job("running", "upscale_video", 0, "Downloading video")
        logger.info(f"Upscale: downloading {req.video_url[:80]}")
        resp = requests.get(req.video_url, timeout=300)
        resp.raise_for_status()
        input_path = os.path.join(work_dir, "input.mp4")
        with open(input_path, "wb") as f:
            f.write(resp.content)
        logger.info(f"Upscale: downloaded {os.path.getsize(input_path) / 1024 / 1024:.1f} MB")

        # 2. Probe video info
        info = _get_video_info(input_path)
        original_w, original_h = info["width"], info["height"]
        fps = info["fps"]
        has_audio = info["has_audio"]
        duration = info["duration"]
        logger.info(f"Upscale: input {original_w}x{original_h} @ {fps} fps, "
                     f"duration={duration}s, audio={has_audio}")

        # 3. Extract frames
        _update_job("running", "upscale_video", 10, "Extracting frames")
        frames_dir = os.path.join(work_dir, "frames")
        os.makedirs(frames_dir)
        extract_result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-qscale:v", "2",
                os.path.join(frames_dir, "frame_%06d.png"),
            ],
            capture_output=True, text=True, timeout=600,
        )
        if extract_result.returncode != 0:
            raise RuntimeError(f"Frame extraction failed: {extract_result.stderr[-500:]}")

        frame_files = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
        total_frames = len(frame_files)
        if total_frames == 0:
            raise RuntimeError("No frames extracted from video")
        logger.info(f"Upscale: extracted {total_frames} frames")

        # 4. Load Real-ESRGAN model
        _update_job("running", "upscale_video", 15, "Loading upscale model")
        models = _load_realesrgan(enhance_faces=req.enhance_faces)
        upsampler = models["upsampler"]
        face_enhancer = models["face_enhancer"]

        # 5. Compute target dimensions
        target_h = req.target_height
        target_w = int(target_h * 16 / 9)
        # Ensure even dimensions for H.264
        target_w = target_w if target_w % 2 == 0 else target_w + 1
        target_h = target_h if target_h % 2 == 0 else target_h + 1

        # 6. Upscale each frame
        upscaled_dir = os.path.join(work_dir, "upscaled")
        os.makedirs(upscaled_dir)

        for i, fname in enumerate(frame_files):
            progress = 20 + int(60 * i / total_frames)
            if i % 50 == 0:
                _update_job("running", "upscale_video", progress,
                            f"Upscaling frame {i+1}/{total_frames}")
                logger.info(f"Upscale: frame {i+1}/{total_frames}")

            frame_path = os.path.join(frames_dir, fname)
            img = cv2.imread(frame_path, cv2.IMREAD_UNCHANGED)

            if face_enhancer is not None:
                _, _, output = face_enhancer.enhance(
                    img, has_aligned=False, only_center_face=False, paste_back=True
                )
            else:
                output, _ = upsampler.enhance(img, outscale=4)

            # Resize to exact target resolution
            output = cv2.resize(output, (target_w, target_h),
                                interpolation=cv2.INTER_LANCZOS4)

            out_path = os.path.join(upscaled_dir, fname)
            cv2.imwrite(out_path, output)

        logger.info(f"Upscale: all {total_frames} frames upscaled to {target_w}x{target_h}")

        # 7. Extract audio from source
        _update_job("running", "upscale_video", 85, "Re-encoding video")
        audio_path = os.path.join(work_dir, "audio.aac")
        audio_extract = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "copy", audio_path],
            capture_output=True, timeout=60,
        )
        audio_ok = (audio_extract.returncode == 0 and
                     os.path.exists(audio_path) and
                     os.path.getsize(audio_path) > 0)

        # 8. Re-encode with ffmpeg (H.264, CRF 18, copy audio)
        output_path = os.path.join(work_dir, "output.mp4")
        encode_cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", os.path.join(upscaled_dir, "frame_%06d.png"),
        ]
        if audio_ok:
            encode_cmd += ["-i", audio_path]

        encode_cmd += [
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
        ]
        if audio_ok:
            encode_cmd += ["-c:a", "copy"]

        encode_cmd += ["-movflags", "+faststart", output_path]

        logger.info("Upscale: re-encoding to video...")
        encode_result = subprocess.run(encode_cmd, capture_output=True, text=True, timeout=600)
        if encode_result.returncode != 0:
            raise RuntimeError(f"Video encoding failed: {encode_result.stderr[-500:]}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError("Encoded output video is missing or too small")

        output_size = os.path.getsize(output_path)
        logger.info(f"Upscale: output video {output_size / 1024 / 1024:.1f} MB at {target_w}x{target_h}")

        # 9. Upload to R2
        _update_job("running", "upscale_video", 95, "Uploading to R2")
        s3 = get_s3()
        s3.upload_file(output_path, R2_BUCKET, req.output_key,
                       ExtraArgs={"ContentType": "video/mp4"})
        video_url = f"{R2_PUBLIC_URL}/{req.output_key}"

        processing_seconds = round(time.time() - t_start, 1)
        _update_job("idle")
        return {
            "video_url": video_url,
            "width": target_w,
            "height": target_h,
            "duration_seconds": round(duration, 1),
            "processing_seconds": processing_seconds,
        }
    except Exception:
        sentry_sdk.capture_exception()
        _update_job("idle")
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/api/upscale-video")
async def upscale_video_endpoint(req: UpscaleVideoRequest):
    if not os.path.exists("/opt/gpu-worker/models/RealESRGAN_x4plus.pth"):
        raise HTTPException(503, "RealESRGAN model weights not found")

    with sentry_sdk.start_span(op="gpu_worker", description="Upscale Video") as span:
        span.set_data("video_url", req.video_url[:80] if req.video_url else "")
        span.set_data("target_height", req.target_height)
        span.set_data("enhance_faces", req.enhance_faces)

    async with gpu_lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: _run_upscale_pipeline(req))
        return result


# ═══════════════════════════════════════════
# Health
# ═══════════════════════════════════════════



# ═══════════════════════════════════════════
# MuseTalk — real-time lip sync
# ═══════════════════════════════════════════

MUSETALK_DIR = "/opt/musetalk"

class MuseTalkRequest(BaseModel):
    face_image_url: str
    audio_url: str
    render_size: str = "240p"
    fps: int = 25

_musetalk_models = None

def _load_musetalk():
    """Load MuseTalk models into VRAM following the hot-swap pattern."""
    global current_model, model_instance, _musetalk_models

    if current_model == "musetalk" and _musetalk_models is not None:
        return _musetalk_models

    _unload_current()

    # Also clear whisper from VRAM if loaded
    global _whisper_model
    if _whisper_model is not None:
        logger.info("Unloading Whisper to free VRAM for MuseTalk")
        del _whisper_model
        _whisper_model = None
        torch.cuda.empty_cache()
        gc.collect()

    logger.info("Loading MuseTalk models...")

    if MUSETALK_DIR not in sys.path:
        sys.path.insert(0, MUSETALK_DIR)

    from musetalk.utils.utils import load_all_model
    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.face_parsing import FaceParsing
    from transformers import WhisperModel

    device = torch.device("cuda:0")

    # MuseTalk uses relative paths internally — temporarily chdir
    _orig_cwd = os.getcwd()
    os.chdir(MUSETALK_DIR)
    try:
        vae, unet, pe = load_all_model(device=device)
        pe = pe.half().to(device)
        vae.vae = vae.vae.half().to(device)
        unet.model = unet.model.half().to(device)

        audio_processor = AudioProcessor(
            feature_extractor_path=os.path.join(MUSETALK_DIR, "models", "whisper")
        )
        whisper_model = WhisperModel.from_pretrained(
            os.path.join(MUSETALK_DIR, "models", "whisper")
        )
        whisper_model = whisper_model.to(device=device, dtype=unet.model.dtype).eval()
        whisper_model.requires_grad_(False)

        fp = FaceParsing()
    finally:
        os.chdir(_orig_cwd)

    _musetalk_models = {
        "vae": vae,
        "unet": unet,
        "pe": pe,
        "audio_processor": audio_processor,
        "whisper": whisper_model,
        "fp": fp,
        "device": device,
    }
    current_model = "musetalk"
    model_instance = _musetalk_models
    logger.info("MuseTalk models loaded (~4GB VRAM)")
    return _musetalk_models


def _run_musetalk_pipeline(req: MuseTalkRequest) -> dict:
    """Run MuseTalk lip-sync inference inline (no subprocess)."""
    import time
    import uuid
    import copy
    import glob as glob_mod

    start = time.time()
    job_id = uuid.uuid4().hex[:16]
    work_dir = os.path.join("/tmp/musetalk_jobs", job_id)
    os.makedirs(work_dir, exist_ok=True)

    try:
        # 1. Download face image
        face_path = os.path.join(work_dir, "face.jpg")
        resp = requests.get(req.face_image_url, timeout=60)
        resp.raise_for_status()
        with open(face_path, "wb") as f:
            f.write(resp.content)

        # 2. Download and convert audio to 16kHz WAV
        raw_audio = os.path.join(work_dir, "raw_audio")
        resp = requests.get(req.audio_url, timeout=60)
        resp.raise_for_status()
        with open(raw_audio, "wb") as f:
            f.write(resp.content)

        audio_path = os.path.join(work_dir, "audio.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_audio,
             "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
            capture_output=True, timeout=30,
        )
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:
            raise RuntimeError("Audio conversion failed")

        # 3. Load models
        models = _load_musetalk()
        vae = models["vae"]
        unet = models["unet"]
        pe = models["pe"]
        audio_processor = models["audio_processor"]
        whisper_model = models["whisper"]
        fp_model = models["fp"]
        device = models["device"]

        # 4. Must add musetalk to path for its internal imports
        if MUSETALK_DIR not in sys.path:
            sys.path.insert(0, MUSETALK_DIR)
        from musetalk.utils.preprocessing import get_landmark_and_bbox, coord_placeholder
        from musetalk.utils.utils import get_file_type, get_video_fps, datagen
        from musetalk.utils.blending import get_image
        import cv2

        # 5. Prepare input image
        input_img_list = [face_path]
        fps = req.fps

        # 6. Extract audio features
        whisper_input_features, librosa_length = audio_processor.get_audio_feature(audio_path)
        whisper_chunks = audio_processor.get_whisper_chunk(
            whisper_input_features, device, unet.model.dtype,
            whisper_model, librosa_length, fps=fps,
        )
        logger.info("MuseTalk: %d whisper chunks from audio", len(whisper_chunks))

        # 7. Detect face and get landmarks (needs CWD = musetalk for model paths)
        _orig_cwd2 = os.getcwd()
        os.chdir(MUSETALK_DIR)
        try:
            coord_list, frame_list = get_landmark_and_bbox(input_img_list, 5)
        finally:
            os.chdir(_orig_cwd2)

        if not coord_list or all(c == coord_placeholder for c in coord_list):
            raise RuntimeError("No face detected in input image")

        # 8. Encode face crop to latent
        timesteps = torch.tensor([0], device=device)
        input_latent_list = []
        for bbox, frame in zip(coord_list, frame_list):
            if bbox == coord_placeholder:
                continue
            x1, y1, x2, y2 = bbox
            crop_frame = frame[y1:y2, x1:x2]
            crop_frame = cv2.resize(crop_frame, (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents = vae.get_latents_for_unet(crop_frame)
            input_latent_list.append(latents)

        if not input_latent_list:
            raise RuntimeError("Failed to encode face crop")

        # 9. Run diffusion inference
        frame_list_cycle = frame_list + frame_list[::-1]
        coord_list_cycle = coord_list + coord_list[::-1]
        input_latent_list_cycle = input_latent_list + input_latent_list[::-1]

        gen = datagen(
            whisper_chunks=whisper_chunks,
            vae_encode_latents=input_latent_list_cycle,
            batch_size=8,
            delay_frame=0,
            device=device,
        )

        res_frame_list = []
        for whisper_batch, latent_batch in gen:
            audio_feature_batch = pe(whisper_batch)
            latent_batch = latent_batch.to(dtype=unet.model.dtype)
            pred_latents = unet.model(
                latent_batch, timesteps,
                encoder_hidden_states=audio_feature_batch,
            ).sample
            recon = vae.decode_latents(pred_latents)
            for res_frame in recon:
                res_frame_list.append(res_frame)

        logger.info("MuseTalk: generated %d frames", len(res_frame_list))

        # 10. Blend generated mouth region onto original frames
        result_img_dir = os.path.join(work_dir, "result_frames")
        os.makedirs(result_img_dir, exist_ok=True)

        for i, res_frame in enumerate(res_frame_list):
            bbox = coord_list_cycle[i % len(coord_list_cycle)]
            ori_frame = copy.deepcopy(frame_list_cycle[i % len(frame_list_cycle)])
            x1, y1, x2, y2 = bbox
            try:
                res_frame = cv2.resize(
                    res_frame.astype(np.uint8), (x2 - x1, y2 - y1)
                )
                combine_frame = get_image(
                    ori_frame, res_frame, [x1, y1, x2, y2], fp=fp_model
                )
                cv2.imwrite(
                    os.path.join(result_img_dir, "%08d.png" % i), combine_frame
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning("MuseTalk blend frame %d failed: %s", i, e)

        # 11. Encode frames to video with audio
        output_path = os.path.join(work_dir, "output.mp4")
        temp_video = os.path.join(work_dir, "temp.mp4")

        subprocess.run([
            "ffmpeg", "-y", "-v", "warning",
            "-r", str(fps), "-f", "image2",
            "-i", os.path.join(result_img_dir, "%08d.png"),
            "-vcodec", "libx264", "-vf", "format=yuv420p",
            "-crf", "18", temp_video,
        ], capture_output=True, timeout=60)

        # Mux audio
        subprocess.run([
            "ffmpeg", "-y", "-v", "warning",
            "-i", audio_path, "-i", temp_video, output_path,
        ], capture_output=True, timeout=60)

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError("MuseTalk video encoding failed")

        # 12. Upload to R2
        r2_key = "musetalk_outputs/%s.mp4" % job_id
        s3 = get_s3()
        s3.upload_file(
            output_path, R2_BUCKET, r2_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )

        render_time = time.time() - start
        logger.info(
            "MuseTalk: %d frames rendered in %.1fs, uploaded to %s",
            len(res_frame_list), render_time, r2_key,
        )

        return {
            "status": "ok",
            "output_r2_key": r2_key,
            "duration_seconds": round(len(res_frame_list) / fps, 1),
            "render_seconds": round(render_time, 1),
        }

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("MuseTalk pipeline failed: %s", e)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/api/musetalk-lipsync")
async def musetalk_endpoint(req: MuseTalkRequest):
    """MuseTalk real-time lip sync endpoint."""
    if not os.path.exists(MUSETALK_DIR):
        raise HTTPException(501, "MuseTalk not installed on this server")

    async with gpu_lock:
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: _run_musetalk_pipeline(req)
            )
            return result
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise HTTPException(
                500, {"status": "error", "error": str(e)[:500]}
            )


@app.get("/api/health")
async def health():
    gpu_name = "unknown"
    vram_total = 0.0
    vram_used = 0.0
    try:
        gpu_name = torch.cuda.get_device_name(0)
        vram_total = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
        vram_used = round(torch.cuda.memory_allocated(0) / 1e9, 1)
    except Exception:
        sentry_sdk.capture_exception()
        pass

    # Check model readiness
    models_ready = {
        "bs_roformer": True,  # Always ready
        "fish_speech": False,  # Stub
        "infinitetalk": (
            os.path.exists(INFINITETALK_WEIGHTS) and
            os.path.exists(os.path.join(WAN_CKPT_DIR, "diffusion_pytorch_model-00001-of-00007.safetensors")) and
            os.path.exists(os.path.join(WAV2VEC_DIR, "model.safetensors"))
        ),
        "realesrgan": os.path.exists("/opt/gpu-worker/models/RealESRGAN_x4plus.pth"),
        "ace_step": os.path.exists("/opt/gpu-worker/ace-step/acestep/pipeline_ace_step.py"),
        "musetalk_installed": os.path.exists("/opt/musetalk/venv/bin/python3"),
        "sdxl_realvisxl": os.path.exists("/opt/gpu-worker/models/sdxl/realvisxl-5/RealVisXL_V5.0_fp16.safetensors"),
    }

    return {
        "status": "ok",
        "gpu": gpu_name,
        "vram_total_gb": vram_total,
        "vram_used_gb": vram_used,
        "loaded_model": current_model,
        "gpu_locked": gpu_lock.locked(),
        "models_ready": models_ready,
    }


@app.get("/api/gpu-status")
async def gpu_status():
    """Lightweight liveness + VRAM probe consumed by the orchestrator's
    HOSTKEY availability check. Returns ``memory_used_mib`` so the
    orchestrator's high-VRAM guard (``_HOSTKEY_VRAM_HIGH_MIB``) can
    reject this host when peak load wouldn't fit. Any 2xx response
    counts as "alive" on the orchestrator side."""
    memory_used_mib: Optional[float] = None
    memory_total_mib: Optional[float] = None
    model_loaded = False
    try:
        memory_used_mib = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
        memory_total_mib = round(
            torch.cuda.get_device_properties(0).total_memory / (1024 * 1024), 1
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)

    try:
        model_loaded = model_instance is not None
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "status": "ok",
        "gpu_busy": gpu_lock.locked(),
        "model_loaded": model_loaded,
        "loaded_model": current_model,
        "memory_used_mib": memory_used_mib,
        "memory_total_mib": memory_total_mib,
    }


# ── ACE-Step Music Generation ──

_ace_step_pipeline = None


class MusicGenerateRequest(BaseModel):
    prompt: str
    lyrics: str = ""
    duration_seconds: float = 60.0
    output_r2_key: str
    lora_r2_key: str = ""
    seed: int = -1
    guidance_scale: float = 15.0
    num_inference_steps: int = 60
    scheduler_type: str = "euler"


class MusicTrainLoraRequest(BaseModel):
    sound_cast_id: str
    training_audio_r2_keys: list
    training_prompts: list
    output_r2_key: str
    steps: int = 100
    learning_rate: float = 1e-4
    rank: int = 16


def _ensure_ace_step():
    """Load ACE-Step pipeline, unloading any other model first."""
    global current_model, model_instance, _ace_step_pipeline
    if current_model == "ace_step" and _ace_step_pipeline is not None:
        return _ace_step_pipeline

    _unload_current()

    logger.info("Loading ACE-Step pipeline...")
    sys.path.insert(0, "/opt/gpu-worker/ace-step")
    from acestep.pipeline_ace_step import ACEStepPipeline

    _ace_step_pipeline = ACEStepPipeline(dtype="bfloat16", cpu_offload=False, overlapped_decode=True)
    current_model = "ace_step"
    model_instance = _ace_step_pipeline
    logger.info("ACE-Step pipeline loaded")
    return _ace_step_pipeline


@app.post("/api/music-generate")
async def music_generate(req: MusicGenerateRequest):
    import time as _time
    start = _time.time()

    async with gpu_lock:
        _unload_current()

        lora_path = ""
        # Download LoRA from R2 if specified
        if req.lora_r2_key:
            lora_path = f"/tmp/lora_{os.path.basename(req.lora_r2_key)}"
            s3 = get_s3()
            s3.download_file(R2_BUCKET, req.lora_r2_key, lora_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Run generation in ACE-Step venv subprocess (avoids diffusers/torch mismatch)
            cmd = [
                "/opt/gpu-worker/ace-step/venv_ace/bin/python3",
                "/opt/gpu-worker/ace_step_generate.py",
                "--prompt", req.prompt,
                "--lyrics", req.lyrics or "",
                "--duration", str(req.duration_seconds),
                "--steps", str(req.num_inference_steps),
                "--guidance_scale", str(req.guidance_scale),
                "--scheduler", req.scheduler_type,
                "--seed", str(req.seed),
                "--lora_path", lora_path,
                "--output_dir", tmpdir,
            ]
            logger.info("ACE-Step generate: duration=%.0fs, prompt=%s", req.duration_seconds, req.prompt[:80])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/opt/gpu-worker/ace-step",
            )
            stdout, stderr = await proc.communicate()
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                sentry_sdk.capture_exception(
                    RuntimeError(f"ACE-Step generate failed (rc={proc.returncode})"),
                    extras={"stderr": stderr_str[-2000:], "stdout": stdout_str[-500:]},
                )
                raise HTTPException(500, f"Music generation failed: {stderr_str[-500:]}")

            # Parse RESULT_JSON from stdout
            result_json = None
            for line in stdout_str.splitlines():
                if line.startswith("RESULT_JSON:"):
                    result_json = json.loads(line[len("RESULT_JSON:"):])
                    break

            if not result_json or "wav_path" not in result_json:
                raise HTTPException(500, "ACE-Step produced no result")

            wav_path = result_json["wav_path"]
            actual_dur = result_json.get("duration_seconds", req.duration_seconds)

            # Upload to R2
            s3 = get_s3()
            s3.upload_file(wav_path, R2_BUCKET, req.output_r2_key,
                           ExtraArgs={"ContentType": "audio/wav"})

            elapsed = _time.time() - start
            logger.info("ACE-Step generate complete: %.1fs, duration=%.1fs", elapsed, actual_dur)
            return {
                "r2_key": req.output_r2_key,
                "url": f"{R2_PUBLIC_URL}/{req.output_r2_key}",
                "duration_seconds": actual_dur,
                "generation_time_seconds": elapsed,
            }



@app.post("/api/music-train-lora")
async def music_train_lora(req: MusicTrainLoraRequest):
    """Train a LoRA adapter on ACE-Step using a raw PyTorch loop.

    Session M replaced the old PyTorch Lightning subprocess approach with
    ace_step_raw_trainer.py — a single-process training script that avoids
    the xformers/linecache recursion, dataset schema mismatches, and
    torchcodec dependency issues.
    """
    import time as _time
    start = _time.time()

    async with gpu_lock:
        _unload_current()  # Free VRAM before training

        s3 = get_s3()
        train_dir = f"/tmp/music_train_{req.sound_cast_id}"
        if os.path.exists(train_dir):
            shutil.rmtree(train_dir)
        os.makedirs(train_dir, exist_ok=True)
        output_dir = os.path.join(train_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        try:
            # Step 1: Download and convert training audio from R2
            audio_paths = []
            for i, key in enumerate(req.training_audio_r2_keys):
                ext = os.path.splitext(key)[-1] or ".wav"
                local = os.path.join(train_dir, f"sample_{i}{ext}")
                s3.download_file(R2_BUCKET, key, local)

                # Convert to standardized 48kHz mono WAV
                wav_path = os.path.join(train_dir, f"sample_{i}_48k.wav")
                subprocess.run([
                    "ffmpeg", "-y", "-v", "error", "-i", local,
                    "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                    wav_path
                ], check=True, capture_output=True)
                audio_paths.append(wav_path)
                logger.info(f"Prepared training audio {i}: {wav_path}")

            # Step 2: Run the raw PyTorch trainer
            prompts = [p if p else "instrumental music" for p in req.training_prompts]
            cmd = [
                "/opt/gpu-worker/ace-step/venv_ace/bin/python3",
                "/opt/gpu-worker/ace_step_raw_trainer.py",
                "--audio_paths", *audio_paths,
                "--prompts", *prompts,
                "--output_dir", output_dir,
                "--checkpoint_dir", "/opt/gpu-worker/ace-step-cache",
                "--max_steps", str(req.steps),
                "--learning_rate", str(req.learning_rate),
                "--lora_rank", str(req.rank),
            ]

            logger.info(f"Starting raw LoRA training: {' '.join(cmd)}")

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd="/opt/gpu-worker",
            )

            # Parse the RESULT_JSON line from stdout
            result_json = None
            for line in proc.stdout.splitlines():
                if line.startswith("RESULT_JSON:"):
                    result_json = json.loads(line[len("RESULT_JSON:"):])
                    break

            if proc.returncode != 0 or not result_json:
                logger.error(f"Raw trainer failed (rc={proc.returncode}). "
                             f"Stderr:\n{proc.stderr[-2000:]}")
                logger.error(f"Stdout tail:\n{proc.stdout[-1000:]}")
                raise HTTPException(500, f"Training failed: {proc.stderr[-500:]}")

            # Step 3: Upload LoRA to R2
            lora_path = result_json["output_path"]
            if not os.path.exists(lora_path):
                raise HTTPException(500, "Trainer reported success but LoRA file missing")

            s3.upload_file(
                lora_path, R2_BUCKET, req.output_r2_key,
                ExtraArgs={"ContentType": "application/octet-stream"}
            )

            elapsed = _time.time() - start
            logger.info(f"LoRA training complete in {elapsed:.0f}s, "
                        f"uploaded to {req.output_r2_key}")

            return {
                "status": "ok",
                "output_r2_key": req.output_r2_key,
                "elapsed_seconds": elapsed,
                "training_steps": result_json["steps_completed"],
                "final_loss": result_json["final_loss"],
                "mean_loss": result_json["mean_loss"],
            }

        except subprocess.TimeoutExpired:
            logger.error("Training timed out")
            raise HTTPException(504, "Training timed out")
        except HTTPException:
            raise
        except Exception as e:
            sentry_sdk.capture_exception(e)
            import traceback
            tb = traceback.format_exc()
            logger.error(f"music-train-lora FAILED:\n{tb}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "error": str(e), "traceback": tb[-2000:]}
            )
        finally:
            shutil.rmtree(train_dir, ignore_errors=True)


# ═══════════════════════════════════════════
# Whisper transcription
# ═══════════════════════════════════════════

class WhisperTranscribeRequest(BaseModel):
    audio_url: str
    language: str = "auto"


# Cached WhisperX models (transcription + wav2vec2 forced alignment)
_whisperx_model = None
_whisperx_align_model = None
_whisperx_align_metadata = None


def _get_whisperx_models():
    global _whisperx_model, _whisperx_align_model, _whisperx_align_metadata
    if _whisperx_model is None:
        import whisperx
        logger.info("Loading WhisperX large-v3...")
        _whisperx_model = whisperx.load_model("large-v3", device="cuda", compute_type="float16")
        logger.info("WhisperX large-v3 loaded")
    if _whisperx_align_model is None:
        import whisperx
        logger.info("Loading WhisperX wav2vec2 alignment model (en)...")
        _whisperx_align_model, _whisperx_align_metadata = whisperx.load_align_model(
            language_code="en", device="cuda"
        )
        logger.info("WhisperX alignment model loaded")
    return _whisperx_model, _whisperx_align_model, _whisperx_align_metadata


def _run_whisper_transcribe(req: WhisperTranscribeRequest) -> dict:
    import time as _time
    import whisperx

    with sentry_sdk.start_span(op="gpu_worker", description="WhisperX transcribe + align") as span:
        try:
            start = _time.time()

            with tempfile.TemporaryDirectory() as tmpdir:
                # Download audio
                logger.info("WhisperX: downloading audio from %s", req.audio_url[:120])
                resp = requests.get(req.audio_url, timeout=120)
                resp.raise_for_status()
                url_path = req.audio_url.split("?")[0]
                ext = os.path.splitext(url_path)[-1] or ".wav"
                audio_path = os.path.join(tmpdir, "input" + ext)
                with open(audio_path, "wb") as f:
                    f.write(resp.content)

                model, align_model, align_metadata = _get_whisperx_models()

                # Step 1: Transcribe with WhisperX
                audio = whisperx.load_audio(audio_path)
                lang = None if req.language == "auto" else req.language
                result = model.transcribe(audio, batch_size=16, language=lang)
                detected_language = result.get("language", req.language if req.language != "auto" else "en")

                # Step 2: Forced alignment via wav2vec2
                aligned = whisperx.align(
                    result["segments"],
                    align_model,
                    align_metadata,
                    audio,
                    device="cuda",
                    return_char_alignments=False,
                )

                # Build segments list
                segments_list = []
                transcript_parts = []
                for seg in aligned.get("segments", []):
                    segments_list.append({
                        "start": round(seg.get("start", 0), 3),
                        "end": round(seg.get("end", 0), 3),
                        "text": seg.get("text", "").strip(),
                    })
                    transcript_parts.append(seg.get("text", "").strip())

                # Build words list from aligned word-level output
                words_list = []
                for seg in aligned.get("segments", []):
                    for w in seg.get("words", []):
                        if "start" in w and "end" in w:
                            words_list.append({
                                "word": w.get("word", "").strip(),
                                "start": round(w["start"], 3),
                                "end": round(w["end"], 3),
                                "score": round(w.get("score", 0.0), 3),
                            })

                # Compute duration from audio length
                duration_seconds = round(len(audio) / 16000, 2)

                transcript = " ".join(transcript_parts)
                elapsed = _time.time() - start
                span.set_data("duration_seconds", duration_seconds)
                span.set_data("word_count", len(words_list))
                span.set_data("elapsed_seconds", round(elapsed, 1))
                logger.info(
                    "WhisperX transcription done in %.1fs, %d chars, %d words with alignment",
                    elapsed, len(transcript), len(words_list),
                )

                return {
                    "status": "ok",
                    "transcript": transcript,
                    "language": detected_language,
                    "duration_seconds": duration_seconds,
                    "segments": segments_list,
                    "words": words_list,
                }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise


@app.post("/api/whisper-transcribe")
async def whisper_transcribe_endpoint(req: WhisperTranscribeRequest):
    async with gpu_lock:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: _run_whisper_transcribe(req))


# ═══════════════════════════════════════════
# Pyannote speaker diarization
# ═══════════════════════════════════════════

class PyannoteRequest(BaseModel):
    audio_url: str
    min_speakers: int = 1
    max_speakers: int = 5


# Cached Pyannote pipeline
_pyannote_pipeline = None


def _get_pyannote():
    global _pyannote_pipeline
    if _pyannote_pipeline is None:
        logger.info("Loading Pyannote speaker-diarization-3.1...")
        from pyannote.audio import Pipeline as PyannPipeline
        hf_token = os.environ.get("HF_TOKEN")
        _pyannote_pipeline = PyannPipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token,
        )
        import torch as _torch; _pyannote_pipeline = _pyannote_pipeline.to(_torch.device("cuda"))
        logger.info("Pyannote pipeline loaded")
    return _pyannote_pipeline


def _run_pyannote_diarize(req: PyannoteRequest) -> dict:
    import time as _time
    start = _time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Download audio
        logger.info("Pyannote: downloading audio")
        resp = requests.get(req.audio_url, timeout=120)
        resp.raise_for_status()
        url_path = req.audio_url.split("?")[0]
        ext = os.path.splitext(url_path)[-1] or ".wav"
        raw_path = os.path.join(tmpdir, "input" + ext)
        with open(raw_path, "wb") as f:
            f.write(resp.content)

        # Convert to 16kHz mono WAV (required by Pyannote)
        audio_path = os.path.join(tmpdir, "input.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", audio_path],
            capture_output=True, timeout=60,
        )
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:
            audio_path = raw_path  # fallback to original if ffmpeg fails

        pipeline = _get_pyannote()
        # Load audio as tensor to bypass torchcodec (broken in this env)
        # Load audio as tensor to bypass torchcodec (broken in this env)
        # Use soundfile+torch instead of torchaudio.load which also uses torchcodec
        import soundfile as sf
        import torch as _torch
        _sf_data, _sf_sr = sf.read(audio_path, dtype="float32", always_2d=True)
        # audio_data shape: (samples, channels) -> convert to (channels, samples)
        waveform = _torch.from_numpy(_sf_data.T)
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        audio_dict = {"waveform": waveform, "sample_rate": _sf_sr}
        diarization = pipeline(
            audio_dict,
            min_speakers=req.min_speakers,
            max_speakers=req.max_speakers,
        )

        # Aggregate segments per speaker
        speaker_segments = {}
        for turn, _, speaker in diarization.speaker_diarization.itertracks(yield_label=True):
            if speaker not in speaker_segments:
                speaker_segments[speaker] = []
            speaker_segments[speaker].append({"start": round(turn.start, 3), "end": round(turn.end, 3)})

        # Calculate total time per speaker
        speaker_totals = {}
        for spk, segs in speaker_segments.items():
            speaker_totals[spk] = sum(s["end"] - s["start"] for s in segs)

        dominant = max(speaker_totals, key=speaker_totals.get) if speaker_totals else ""
        dominant_total = round(speaker_totals.get(dominant, 0.0), 2)

        speakers_out = [
            {"speaker": spk, "segments": segs}
            for spk, segs in sorted(speaker_segments.items())
        ]

        elapsed = _time.time() - start
        logger.info("Pyannote diarization done in %.1fs, %d speakers" % (elapsed, len(speakers_out)))

        return {
            "status": "ok",
            "speakers": speakers_out,
            "dominant_speaker": dominant,
            "dominant_speaker_total_seconds": dominant_total,
        }


@app.post("/api/pyannote-diarize")
async def pyannote_diarize_endpoint(req: PyannoteRequest):
    async with gpu_lock:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: _run_pyannote_diarize(req))


# ── SDXL Image Generation ──
# Scoped to My Videos | Photos Generated sub-folder feature only.
# Pipeline loads with safety_checker=None — content moderation is handled outside.
# Uses hot-swap pattern: mutually exclusive with other large models on GPU.

SDXL_MODELS_DIR = "/opt/gpu-worker/models/sdxl"

_sdxl_pipeline = None
_sdxl_variant: Optional[str] = None


def _load_sdxl(variant: str = "realvisxl_5"):
    global current_model, model_instance, _sdxl_pipeline, _sdxl_variant
    if _sdxl_variant == variant and _sdxl_pipeline is not None:
        return _sdxl_pipeline
    _unload_current()
    if _sdxl_pipeline is not None:
        logger.info(f"SDXL: unloading previous variant {_sdxl_variant}")
        del _sdxl_pipeline
        _sdxl_pipeline = None
        torch.cuda.empty_cache()

    from diffusers import StableDiffusionXLPipeline, AutoencoderKL

    logger.info(f"SDXL: loading variant {variant}")
    if variant == "sdxl_base":
        pipe = StableDiffusionXLPipeline.from_pretrained(
            f"{SDXL_MODELS_DIR}/base",
            torch_dtype=torch.float16, variant="fp16",
            use_safetensors=True, safety_checker=None, add_watermarker=False,
        )
    elif variant == "realvisxl_5":
        pipe = StableDiffusionXLPipeline.from_single_file(
            f"{SDXL_MODELS_DIR}/realvisxl-5/RealVisXL_V5.0_fp16.safetensors",
            torch_dtype=torch.float16, use_safetensors=True,
            safety_checker=None, add_watermarker=False,
        )
    else:
        raise ValueError(f"Unknown SDXL variant: {variant}")

    pipe.vae = AutoencoderKL.from_pretrained(
        f"{SDXL_MODELS_DIR}/vae-fp16-fix", torch_dtype=torch.float16,
    )
    pipe.enable_attention_slicing(slice_size="auto")
    pipe = pipe.to("cuda")

    try:
        with torch.inference_mode():
            pipe(prompt="warmup", num_inference_steps=2, height=512, width=512, output_type="latent")
    except Exception as e:
        logger.warning(f"SDXL warmup skipped: {e}")

    _sdxl_pipeline = pipe
    _sdxl_variant = variant
    current_model = "sdxl"
    model_instance = pipe
    logger.info(f"SDXL {variant} loaded, VRAM: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")
    return pipe


class SDXLGenerateRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    model_variant: str = "realvisxl_5"
    width: int = 1024
    height: int = 1024
    num_inference_steps: int = 25
    guidance_scale: Optional[float] = None
    seed: Optional[int] = None
    num_images: int = 1


def _run_sdxl_generate(req: SDXLGenerateRequest) -> dict:
    import io
    import base64

    pipe = _load_sdxl(req.model_variant)

    default_cfg = 5.0 if req.model_variant == "realvisxl_5" else 7.5
    cfg = req.guidance_scale if req.guidance_scale is not None else default_cfg
    num_images = min(req.num_images, 4)

    generator = None
    if req.seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(req.seed)

    negative_prompt = req.negative_prompt or (
        "low quality, blurry, pixelated, distorted, bad anatomy, watermark, text, "
        "deformed, disfigured, extra limbs"
    )

    with torch.inference_mode():
        result = pipe(
            prompt=req.prompt, negative_prompt=negative_prompt,
            num_inference_steps=req.num_inference_steps, guidance_scale=cfg,
            width=req.width, height=req.height,
            num_images_per_prompt=num_images, generator=generator,
        )

    images_b64 = []
    for img in result.images:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        images_b64.append(base64.b64encode(buf.getvalue()).decode())

    return {
        "images_base64": images_b64,
        "format": "jpeg",
        "count": len(images_b64),
    }


@app.post("/api/sdxl-generate")
async def sdxl_generate_endpoint(req: SDXLGenerateRequest):
    async with gpu_lock:
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: _run_sdxl_generate(req))
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("SDXL generation failed")
            raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════
# FFmpeg Compose — Pass 2 of two-pass render
# ═══════════════════════════════════════════


class FFmpegComposeInput(BaseModel):
    url: str
    label: str


class FFmpegComposeRequest(BaseModel):
    render_id: str
    cast_id: str
    timeline: dict
    baked_urls: dict  # element_id -> baked MP4 URL
    # PR #80: slot-aligned compose inputs. Each entry is {url, s, e, block_id}.
    # When present, the slot-aligned compose path (worker_ffmpeg_compose.py)
    # lays each clip / audio at its absolute s offset on the canvas instead
    # of concatenating end-to-end. Optional for back-compat with older
    # orchestrators that haven't been redeployed.
    compose_video_tracks: list = []
    compose_audio_tracks: list = []
    overlay_elements: list = []


def _get_canvas_size(timeline: dict) -> tuple[int, int]:
    """Infer canvas size from the timeline or default to 1080x1920."""
    # Check if any element has frame dimensions
    for track in timeline.get("tracks", []):
        for el in track.get("elements", []):
            frame = el.get("frame", {})
            if frame.get("width") and frame.get("height"):
                return frame["width"], frame["height"]
    return 1080, 1920


def _translate_timeline(timeline: dict, baked_urls: dict, render_id: str, cw: int, ch: int) -> dict:
    """Translate timeline to FFmpeg composition plan (inline version for GPU worker).

    Returns dict with: inputs, filter_complex, output_map, output_key
    """
    tracks = timeline.get("tracks", [])
    inputs = []  # list of {url, label, has_video, has_audio}
    segment_labels = []
    music_inputs = []
    music_labels = []
    overlay_elements = []
    caption_elements = []
    filters = []

    # Collect bonded V1 segments (skip A1 voice)
    bonded_segments = []
    for track in tracks:
        for el in track.get("elements", []):
            meta = el.get("metadata") or {}
            # A1 voice — SKIP (audio is intrinsic to baked MP4)
            if meta.get("bonded") and meta.get("paired_video_element_id"):
                continue
            # Bonded V1 snapshot — will be replaced with baked MP4
            if meta.get("bonded") and meta.get("block_id") and meta.get("paired_audio_element_id"):
                bonded_segments.append(el)
                continue
            # Music
            if track.get("type") == "audio" and not meta.get("bonded"):
                src = el.get("props", {}).get("src", "")
                if src:
                    music_inputs.append(el)
                continue
            # Captions
            if track.get("type") == "caption" or el.get("type") == "caption":
                caption_elements.append(el)
                continue
            # Image overlays (products, etc.)
            if el.get("type") == "image" and track.get("type") in ("element", "video"):
                meta2 = el.get("metadata") or {}
                if not meta2.get("bonded"):
                    overlay_elements.append(el)
                continue

    bonded_segments.sort(key=lambda e: e.get("s", 0))

    # Build inputs for bonded segments
    input_idx = 0
    for seg in bonded_segments:
        eid = seg["id"]
        url = baked_urls.get(eid, seg.get("props", {}).get("src", ""))
        if not url:
            continue
        label = f"seg{input_idx}"
        inputs.append({"url": url, "label": label})
        segment_labels.append((f"{label}_v", f"{label}_a"))
        input_idx += 1

    # Scale + pad each segment
    for i, (v_label, a_label) in enumerate(segment_labels):
        filters.append(
            f"[{i}:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
            f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1[{v_label}]"
        )
        filters.append(f"[{i}:a]anull[{a_label}]")

    # Concat
    if segment_labels:
        concat_in = "".join(f"[{v}][{a}]" for v, a in segment_labels)
        n = len(segment_labels)
        filters.append(f"{concat_in}concat=n={n}:v=1:a=1[timeline_v][timeline_a]")
    else:
        filters.append(f"color=c=black:s={cw}x{ch}:d=5[timeline_v]")
        filters.append(f"anullsrc=cl=stereo:r=44100[timeline_a]")

    current_v = "timeline_v"

    # Image overlays
    for idx, el in enumerate(overlay_elements):
        src = el.get("props", {}).get("src", "")
        if not src:
            continue
        label = f"img{idx}"
        real_idx = len(inputs)
        inputs.append({"url": src, "label": label})
        frame = el.get("frame", {})
        x = frame.get("x", 0)
        y = frame.get("y", 0)
        w = frame.get("width", 200)
        h = frame.get("height", 200)
        start = el.get("s", 0)
        end = el.get("e", 5)
        out = f"ov_{idx}"
        filters.append(f"[{real_idx}:v]scale={w}:{h}[{label}_s]")
        filters.append(
            f"[{current_v}][{label}_s]overlay=x={x}:y={y}"
            f":enable='between(t,{start},{end})'[{out}]"
        )
        current_v = out

    # Captions via drawtext
    for idx, cap in enumerate(caption_elements):
        props = cap.get("props", {})
        text = (props.get("text", "") or "").replace("'", "'\\''").replace(":", "\\:")
        font_size = props.get("fontSize", 36)
        font_color = props.get("fill", "white")
        frame = cap.get("frame", {})
        y_pos = frame.get("y", int(ch * 0.7))
        start = cap.get("s", 0)
        end = cap.get("e", 1)
        out = f"cap_{idx}"
        filters.append(
            f"[{current_v}]drawtext="
            f"text='{text}':"
            f"fontsize={font_size}:"
            f"fontcolor={font_color}:"
            f"borderw=2:bordercolor=black:"
            f"x=(w-text_w)/2:y={y_pos}:"
            f"enable='between(t,{start},{end})'"
            f"[{out}]"
        )
        current_v = out

    final_v = current_v

    # Music sidechain ducking
    if music_inputs:
        music_real_indices = []
        for mus in music_inputs:
            src = mus.get("props", {}).get("src", "")
            label = f"mus{len(music_real_indices)}"
            real_idx = len(inputs)
            inputs.append({"url": src, "label": label})
            music_real_indices.append(real_idx)
            music_labels.append(label)

        filters.append("[timeline_a]asplit[narration_main][narration_sidechain]")

        if len(music_labels) == 1:
            midx = music_real_indices[0]
            filters.append(f"[{midx}:a]volume=0.15[music_raw]")
        else:
            for i, midx in enumerate(music_real_indices):
                filters.append(f"[{midx}:a]volume=0.15[mus_vol_{i}]")
            mus_mix = "".join(f"[mus_vol_{i}]" for i in range(len(music_labels)))
            filters.append(f"{mus_mix}amix=inputs={len(music_labels)}:duration=longest[music_raw]")

        filters.append(
            "[music_raw][narration_sidechain]sidechaincompress="
            "threshold=0.015:ratio=15:attack=30:release=800[music_ducked]"
        )
        filters.append("[narration_main][music_ducked]amix=inputs=2:duration=longest[final_a]")
        final_a = "final_a"
    else:
        final_a = "timeline_a"

    filter_complex = ";\n".join(filters)
    output_key = f"renders/{render_id}/final.mp4"

    return {
        "inputs": inputs,
        "filter_complex": filter_complex,
        "output_map": [f"[{final_v}]", f"[{final_a}]"],
        "output_key": output_key,
    }


def _run_ffmpeg_compose(req: FFmpegComposeRequest) -> dict:
    """Download inputs, run FFmpeg with nvenc, upload result to R2."""
    import time
    start_time = time.time()

    render_id = req.render_id
    timeline = req.timeline
    baked_urls = req.baked_urls
    cw, ch = _get_canvas_size(timeline)

    # Translate timeline to FFmpeg plan
    plan = _translate_timeline(timeline, baked_urls, render_id, cw, ch)

    work_dir = tempfile.mkdtemp(prefix=f"ffmpeg_{render_id}_")
    logger.info(f"FFmpeg compose: work_dir={work_dir}, inputs={len(plan['inputs'])}")

    try:
        # Download all inputs
        input_files = {}
        for idx, inp in enumerate(plan["inputs"]):
            ext = ".mp4" if "mp4" in inp["url"].lower() else ".mp4"
            local_path = os.path.join(work_dir, f"input_{idx}{ext}")
            logger.info(f"Downloading input {inp['label']}: {inp['url'][:100]}")
            resp = requests.get(inp["url"], timeout=120)
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(resp.content)
            input_files[inp["label"]] = local_path

        # Build FFmpeg command
        output_path = os.path.join(work_dir, "output.mp4")
        cmd = ["ffmpeg", "-y"]

        for idx, inp in enumerate(plan["inputs"]):
            local_path = input_files[inp["label"]]
            cmd.extend(["-i", local_path])

        cmd.extend(["-filter_complex", plan["filter_complex"]])

        for m in plan["output_map"]:
            cmd.extend(["-map", m])

        # Try nvenc first, fall back to libx264
        use_nvenc = True
        try:
            result = subprocess.run(
                ["ffmpeg", "-encoders"],
                capture_output=True, text=True, timeout=10,
            )
            if "h264_nvenc" not in result.stdout:
                use_nvenc = False
        except Exception:
            use_nvenc = False

        if use_nvenc:
            cmd.extend([
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-cq", "20",
                "-b:v", "4000k",
                "-maxrate", "5000k",
            ])
        else:
            logger.warning("h264_nvenc not available, falling back to libx264")
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "20",
                "-b:v", "4000k",
                "-maxrate", "5000k",
            ])

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level", "4.1",
            "-movflags", "+faststart",
            output_path,
        ])

        logger.info(f"Running FFmpeg: {' '.join(cmd[:20])}...")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg stderr: {result.stderr[-2000:]}")
            raise RuntimeError(f"FFmpeg failed (exit {result.returncode}): {result.stderr[-500:]}")

        if not os.path.exists(output_path):
            raise RuntimeError("FFmpeg produced no output file")

        output_size = os.path.getsize(output_path)
        logger.info(f"FFmpeg output: {output_size} bytes")

        # Upload to R2
        output_key = plan["output_key"]
        s3 = get_s3()
        s3.upload_file(
            output_path, R2_BUCKET, output_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )

        elapsed = time.time() - start_time
        public_url = f"{R2_PUBLIC_URL}/{output_key}"
        logger.info(f"FFmpeg compose complete: {output_key} ({elapsed:.1f}s)")

        return {
            "output_r2_key": output_key,
            "output_url": public_url,
            "output_size_bytes": output_size,
            "elapsed_seconds": round(elapsed, 1),
            "encoder": "h264_nvenc" if use_nvenc else "libx264",
        }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/api/ffmpeg-compose")
async def ffmpeg_compose_endpoint(req: FFmpegComposeRequest):
    """Pass 2 of the two-pass render pipeline.

    Downloads baked MP4s + music, runs FFmpeg with nvenc for timeline composition,
    uploads result to R2. A1 voice is SKIPPED — audio is intrinsic to baked MP4s.
    """
    # FFmpeg doesn't need GPU memory (uses nvenc hardware encoder only),
    # but we still serialize to avoid concurrent heavy I/O
    try:
        loop = asyncio.get_event_loop()
        use_v2 = bool(
            getattr(req, "compose_video_tracks", None)
            or getattr(req, "compose_audio_tracks", None)
        )
        if not use_v2:
            for _u in (getattr(req, "baked_urls", None) or {}).values():
                if isinstance(_u, str) and _u.split("?", 1)[0].lower().endswith(
                    (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
                ):
                    use_v2 = True
                    break
        fn = _run_ffmpeg_compose_v2 if use_v2 else _run_ffmpeg_compose
        return await loop.run_in_executor(None, lambda: fn(req))
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("FFmpeg compose failed")
        raise HTTPException(status_code=500, detail=str(e))
