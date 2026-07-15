"""
MuseTalk HTTP wrapper for the LuminaCast render pipeline.

A small FastAPI app that loads MuseTalk models once at startup, then
serves /render: takes {image_url, audio_url, optional bbox_shift} and
returns the lip-synced video bytes (mp4).

We skip the realtime path (which requires per-avatar prep + saved
latents) because our use case is one-shot stateless rendering.
inference.py supports a single-image video_path and that's sufficient.

Lifecycle:
  - Models load on first request (lazy) so service starts fast and
    doesn't hold GPU memory if no PIP block ever rolls through.
  - A simple file lock stops two requests fighting over the GPU.
  - Inputs are downloaded to a tempdir; outputs are streamed back.

Compatible with the dispatcher contract used by the InfiniteTalk worker
on the same machine — same {image_url, audio_url} input shape, same
mp4 output, so render_dispatcher only needs a thin "if pip then
musetalk endpoint" branch.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Ensure musetalk imports resolve when this file is run from outside its dir.
MUSETALK_DIR = Path("/opt/musetalk")
sys.path.insert(0, str(MUSETALK_DIR))
os.chdir(MUSETALK_DIR)  # MuseTalk uses relative paths in defaults

PORT = int(os.environ.get("MUSETALK_PORT", "7861"))
GPU_LOCK = threading.Lock()
LOCK_OWNER: dict = {"pid": None, "since": None}

# Lazy-loaded model handles, populated on first /render call.
_MODELS = {"vae": None, "unet": None, "pe": None, "whisper": None,
           "fp": None, "audio_processor": None, "device": None,
           "weight_dtype": None}


def _load_models() -> None:
    """One-shot model load. Re-entrant; no-op if already loaded."""
    if _MODELS["unet"] is not None:
        return
    print("[musetalk] loading models …", flush=True)
    t0 = time.time()
    import torch
    from transformers import WhisperModel
    from musetalk.utils.face_parsing import FaceParsing
    from musetalk.utils.utils import load_all_model
    from musetalk.utils.audio_processor import AudioProcessor

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    vae, unet, pe = load_all_model(
        unet_model_path=str(MUSETALK_DIR / "models/musetalkV15/unet.pth"),
        vae_type="sd-vae",
        unet_config=str(MUSETALK_DIR / "models/musetalkV15/musetalk.json"),
        device=device,
    )
    pe = pe.to(device)
    vae.vae = vae.vae.to(device)
    unet.model = unet.model.to(device)
    weight_dtype = unet.model.dtype

    audio_processor = AudioProcessor(feature_extractor_path=str(MUSETALK_DIR / "models/whisper"))
    whisper = WhisperModel.from_pretrained(str(MUSETALK_DIR / "models/whisper"))
    whisper = whisper.to(device=device, dtype=weight_dtype).eval()
    whisper.requires_grad_(False)

    fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)

    _MODELS.update(
        vae=vae, unet=unet, pe=pe, whisper=whisper,
        fp=fp, audio_processor=audio_processor, device=device,
        weight_dtype=weight_dtype,
    )
    print(f"[musetalk] models loaded in {time.time()-t0:.1f}s", flush=True)


def _download(url: str, dest: Path, timeout: float = 60.0) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise HTTPException(502, f"download failed: HTTP {r.status_code} for {url[:120]}")
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(64 * 1024):
                f.write(chunk)


class RenderRequest(BaseModel):
    image_url: str
    audio_url: str
    width: int | None = None  # ignored for now \u2014 MuseTalk preserves source ratio
    height: int | None = None
    prompt: str | None = None  # accepted for interface parity, not used


def _run_inference(image_path: Path, audio_path: Path, out_path: Path) -> None:
    """Run a single MuseTalk inference task. Mirrors scripts/inference.py.main()
    but without the YAML-config indirection \u2014 we have one task at a time."""
    import torch
    import cv2
    import glob
    import pickle
    import numpy as np
    from tqdm import tqdm
    from musetalk.utils.utils import datagen
    from musetalk.utils.preprocessing import get_landmark_and_bbox
    from musetalk.utils.blending import get_image_prepare_material, get_image_blending

    vae = _MODELS["vae"]
    unet = _MODELS["unet"]
    pe = _MODELS["pe"]
    whisper = _MODELS["whisper"]
    fp = _MODELS["fp"]
    audio_processor = _MODELS["audio_processor"]
    device = _MODELS["device"]
    weight_dtype = _MODELS["weight_dtype"]

    # Single-image input \u2014 MuseTalk loops it for the audio duration.
    input_img_list = [str(image_path)]
    fps = 25  # MuseTalk default; matches our composer's 30 fps after re-encode

    # Whisper feature extraction
    whisper_features, librosa_length = audio_processor.get_audio_feature(str(audio_path))
    whisper_chunks = audio_processor.get_whisper_chunk(
        whisper_features, device, weight_dtype, whisper, librosa_length,
        fps=fps, audio_padding_length_left=2, audio_padding_length_right=2,
    )

    # Face landmark + bbox extraction. bbox_shift=0 for v15 per inference.sh.
    coord_list, frame_list = get_landmark_and_bbox(input_img_list, bbox_shift=0)

    # Build the latent input list. With one source frame we cycle it.
    i = 0
    input_latent_list = []
    for bbox, frame in zip(coord_list, frame_list):
        if bbox == "coord_placeholder":
            continue
        x1, y1, x2, y2 = bbox
        # extra_margin is added during inference for v15 (default 10px below jaw)
        extra = 10
        y2 = min(y2 + extra, frame.shape[0])
        crop_frame = frame[y1:y2, x1:x2]
        crop_frame = cv2.resize(crop_frame, (256, 256), interpolation=cv2.INTER_LANCZOS4)
        latents = vae.get_latents_for_unet(crop_frame)
        input_latent_list.append(latents)

    if not input_latent_list:
        raise RuntimeError("no faces detected in source image")

    # For one source frame we just keep cycling that single latent
    input_latent_list_cycle = input_latent_list + input_latent_list[::-1]
    frame_list_cycle = frame_list + frame_list[::-1]
    coord_list_cycle = coord_list + coord_list[::-1]

    video_num = len(whisper_chunks)
    batch_size = 8
    gen = datagen(whisper_chunks, input_latent_list_cycle, batch_size)

    res_frame_list = []
    for i_batch, (whisper_batch, latent_batch) in enumerate(
        tqdm(gen, total=int(np.ceil(float(video_num) / batch_size)))
    ):
        audio_feature_batch = pe(whisper_batch.to(device, dtype=weight_dtype))
        latent_batch = latent_batch.to(device=device, dtype=weight_dtype)
        pred_latents = unet.model(latent_batch, torch.tensor([0], device=device),
                                  encoder_hidden_states=audio_feature_batch).sample
        recon = vae.decode_latents(pred_latents)
        for r in recon:
            res_frame_list.append(r)

    # Composite generated face crops back into the source frame; write video.
    tmp_imgs = out_path.parent / f"frames_{uuid.uuid4().hex[:8]}"
    tmp_imgs.mkdir(parents=True, exist_ok=True)
    for i, res_frame in enumerate(tqdm(res_frame_list)):
        bbox = coord_list_cycle[i % len(coord_list_cycle)]
        ori_frame = frame_list_cycle[i % len(frame_list_cycle)].copy()
        x1, y1, x2, y2 = bbox
        try:
            res_frame = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, (y2 + 10) - y1))
        except Exception:
            continue
        # Paste back via blending
        mask = get_image_prepare_material(ori_frame, [x1, y1, x2, y2], fp=fp)
        combined = get_image_blending(ori_frame, res_frame, [x1, y1, x2, y2 + 10], mask, fp=fp)
        cv2.imwrite(str(tmp_imgs / f"{i:08d}.png"), combined)

    # Encode silent video then mux audio
    silent = out_path.parent / "silent.mp4"
    cmd = (
        f"ffmpeg -y -loglevel error -framerate {fps} -i {tmp_imgs}/%08d.png "
        f"-c:v libx264 -pix_fmt yuv420p -crf 18 {silent}"
    )
    os.system(cmd)
    os.system(
        f"ffmpeg -y -loglevel error -i {silent} -i {audio_path} "
        f"-c:v copy -c:a aac -b:a 128k -shortest {out_path}"
    )
    silent.unlink(missing_ok=True)


app = FastAPI(title="LuminaCast MuseTalk")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "models_loaded": _MODELS["unet"] is not None,
        "lock_owner": LOCK_OWNER,
    }


@app.post("/render")
def render(req: RenderRequest):
    """Lip-sync `image_url` to `audio_url` and return the mp4 binary.

    GPU-serialized via a process-wide lock so two requests never collide.
    Returns the mp4 bytes directly so the dispatcher's flow matches the
    InfiniteTalk path (which also streams bytes).
    """
    acquired = GPU_LOCK.acquire(timeout=5)
    if not acquired:
        raise HTTPException(503, "musetalk busy with another request")
    LOCK_OWNER["pid"] = os.getpid()
    LOCK_OWNER["since"] = time.time()
    persistent = Path("/var/lib/musetalk-out")
    persistent.mkdir(parents=True, exist_ok=True)
    final = persistent / f"{uuid.uuid4().hex}.mp4"
    try:
        _load_models()
        with tempfile.TemporaryDirectory(prefix="muse_") as td:
            td_p = Path(td)
            img_path = td_p / "face.jpg"
            aud_path = td_p / "audio.wav"
            out_path = td_p / "out.mp4"
            _download(req.image_url, img_path)
            _download(req.audio_url, aud_path)
            t0 = time.time()
            _run_inference(img_path, aud_path, out_path)
            dt = time.time() - t0
            print(f"[musetalk] render done in {dt:.1f}s", flush=True)
            out_path.replace(final)
        return FileResponse(final, media_type="video/mp4", filename="musetalk.mp4")
    finally:
        LOCK_OWNER["pid"] = None
        LOCK_OWNER["since"] = None
        GPU_LOCK.release()
