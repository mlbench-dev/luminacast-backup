"""
Modal serverless InfiniteTalk renderer — Tier 2 of the HOSTKEY → Modal → RunPod cascade.

Same API as the HOSTKEY GPU worker:
  POST  https://WORKSPACE--luminacast-infinitetalk-render.modal.run/
  Body: {"image_url": "...", "audio_url": "...", "prompt": "...", "width": 480, "height": 848}
  Returns: {"video": "<base64 mp4>", "size_bytes": int}

Deploy:
  modal deploy modal_infinitetalk.py
"""
import modal
import os
import json
import time
import uuid
import copy
import base64
import subprocess

# ── Image build: CUDA + ComfyUI + custom nodes ──
comfyui_image = (
    modal.Image.from_registry("nvidia/cuda:12.1.1-runtime-ubuntu22.04", add_python="3.11")
    .apt_install("git", "wget", "ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install(
        "torch==2.4.0",
        "torchvision==0.19.0",
        "torchaudio==2.4.0",
        extra_index_url="https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "fastapi[standard]",
        "httpx",
        "websockets",
        "Pillow",
        "numpy<2",
        "scipy",
        "requests",
        "huggingface-hub[hf_xet]>=0.24",
        "safetensors",
        "accelerate",
        "transformers",
        "diffusers",
        "einops",
        "opencv-python-headless",
        "imageio-ffmpeg",
        "sentencepiece",
        "protobuf",
        "awscli",
    )
    .run_commands(
        # Clone ComfyUI
        "git clone https://github.com/comfyanonymous/ComfyUI /comfyui",
        "cd /comfyui && pip install --no-cache-dir -r requirements.txt",
        # Custom nodes needed by InfiniteTalk workflow
        "cd /comfyui/custom_nodes && git clone https://github.com/kijai/ComfyUI-WanVideoWrapper",
        "cd /comfyui/custom_nodes && git clone https://github.com/kijai/ComfyUI-KJNodes",
        "cd /comfyui/custom_nodes && git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
        # MelBandRoFormer provides audio-source-separation nodes used by our
        # InfiniteTalk workflow (node class 'MelBandRoFormerSampler'). Without
        # this, ComfyUI rejects the /prompt POST with 400 'missing_node_type'.
        "cd /comfyui/custom_nodes && git clone https://github.com/kijai/ComfyUI-MelBandRoFormer",
        # Install node requirements (ignore errors for optional deps)
        "cd /comfyui/custom_nodes/ComfyUI-WanVideoWrapper && pip install --no-cache-dir -r requirements.txt 2>/dev/null || true",
        "cd /comfyui/custom_nodes/ComfyUI-KJNodes && pip install --no-cache-dir -r requirements.txt 2>/dev/null || true",
        "cd /comfyui/custom_nodes/ComfyUI-VideoHelperSuite && pip install --no-cache-dir -r requirements.txt 2>/dev/null || true",
        "cd /comfyui/custom_nodes/ComfyUI-MelBandRoFormer && pip install --no-cache-dir -r requirements.txt 2>/dev/null || true",
    )
    .add_local_file("api_workflow.json", "/workflow/api_workflow.json")
)

# Persistent volume for the ~33GB of model weights — downloaded once, shared across runs
model_volume = modal.Volume.from_name("infinitetalk-models", create_if_missing=True)

app = modal.App("luminacast-infinitetalk", image=comfyui_image)


def download_models_if_needed(models_dir: str):
    """Sync all InfiniteTalk weights from Cloudflare R2 to the persistent volume.
    Runs once on first cold start; subsequent starts see weights already present.
    """
    import subprocess

    # R2 credentials (read-only bucket access)
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": "b728d1ef06a0f1bdb4954080bcf3052d",
        "AWS_SECRET_ACCESS_KEY": "8f119da9f392d1361650b81ff011362acbd8f7058cc36140e3ce263770dec885",
        "AWS_DEFAULT_REGION": "auto",
    }
    R2_ENDPOINT = "https://0adcb995bfca0f652d6b86f053a6de8f.r2.cloudflarestorage.com"
    R2_URL = "s3://luminacast/infinitetalk-models/"

    # Check if we already have the key files cached (fast cold-start skip)
    critical_files = [
        f"{models_dir}/diffusion_models/wan2.1_I2V_480p_14B_fp8_e4m3fn_scaled.safetensors",
        f"{models_dir}/diffusion_models/Wan2_1-InfiniteTalk-Single_fp8_e4m3fn_scaled_KJ.safetensors",
        f"{models_dir}/text_encoders/umt5-xxl-enc-bf16.safetensors",
        f"{models_dir}/clip_vision/clip_vision_h.safetensors",
        f"{models_dir}/vae/wan_2.1_vae.safetensors",
        f"{models_dir}/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
    ]
    all_present = all(
        os.path.exists(p) and os.path.getsize(p) > 100_000_000 for p in critical_files
    )
    if all_present:
        print("[cache-hit] All critical models already present, skipping R2 sync")
        return

    print(f"[sync] R2 {R2_URL} -> {models_dir}")
    os.makedirs(models_dir, exist_ok=True)

    # Install aws CLI if needed
    try:
        subprocess.run(["aws", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Installing aws CLI...")
        subprocess.run(
            ["pip", "install", "--quiet", "awscli"],
            check=True,
        )

    # Sync everything from R2 to the volume
    result = subprocess.run(
        [
            "aws", "s3", "sync",
            R2_URL,
            models_dir,
            "--endpoint-url", R2_ENDPOINT,
            "--only-show-errors",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.returncode != 0:
        print(f"[sync] ERROR: {result.stderr[:2000]}")
    else:
        print("[sync] R2 sync complete")

    # Report what we got
    for f in critical_files:
        if os.path.exists(f):
            print(f"  OK {os.path.basename(f)} ({os.path.getsize(f) / 1e9:.1f} GB)")
        else:
            print(f"  MISSING {os.path.basename(f)}")


@app.cls(
    gpu="L40S",  # InfiniteTalk needs ~22GB VRAM; L40S has 48GB
    volumes={"/models": model_volume},
    timeout=1800,  # 30 min per request
    scaledown_window=300,  # 5 min idle before shutdown
    max_containers=5,  # Up to 5 parallel blocks
)
@modal.concurrent(max_inputs=1)  # Each container processes one render at a time
class InfiniteTalkRenderer:

    @modal.enter()
    def setup(self):
        """Cold start: download models if needed, start ComfyUI."""
        import httpx

        # Step 1: Ensure models are on the volume
        download_models_if_needed("/models")
        model_volume.commit()

        # Step 2: Symlink models into ComfyUI's models dir
        for subdir in ["diffusion_models", "text_encoders", "clip_vision", "vae", "loras", "transformers", "wav2vec2"]:
            src = f"/models/{subdir}"
            dst = f"/comfyui/models/{subdir}"
            os.makedirs(src, exist_ok=True)
            # Remove any existing dir/symlink at dst
            if os.path.lexists(dst):
                if os.path.islink(dst):
                    os.unlink(dst)
                else:
                    import shutil
                    shutil.rmtree(dst, ignore_errors=True)
            os.symlink(src, dst)

        # Step 3: Start ComfyUI in background
        self.comfyui_proc = subprocess.Popen(
            ["python", "main.py", "--listen", "127.0.0.1", "--port", "8188", "--dont-print-server"],
            cwd="/comfyui",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Step 4: Wait for ComfyUI to be ready
        ready = False
        for i in range(180):
            try:
                resp = httpx.get("http://127.0.0.1:8188/", timeout=2)
                if resp.status_code == 200:
                    print(f"ComfyUI ready after {i}s")
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(1)
        if not ready:
            try:
                out = self.comfyui_proc.stdout.read(5000).decode("utf-8", errors="ignore") if self.comfyui_proc.stdout else ""
            except Exception:
                out = "(unable to read stdout)"
            raise RuntimeError(f"ComfyUI failed to start within 180s. Last output:\n{out}")

        # Step 4b: Drain ComfyUI stdout so import errors in custom nodes are
        # visible in Modal logs. Without this any MelBandRoFormer / WanVideo
        # import traceback goes to an unread PIPE and the only symptom we see
        # is a silent 400 from /prompt. Reads up to 200KB then stops so the
        # subprocess can keep producing output without blocking.
        try:
            import fcntl
            if self.comfyui_proc.stdout is not None:
                fcntl.fcntl(self.comfyui_proc.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
                startup_out = self.comfyui_proc.stdout.read(200000)
                if startup_out:
                    print("--- ComfyUI startup output (truncated 200KB) ---")
                    print(startup_out.decode("utf-8", errors="ignore"))
                    print("--- end ComfyUI startup output ---")
        except Exception as e:
            print(f"(could not drain ComfyUI stdout: {e})")

        # Step 4c: Probe registered node types so we can confirm MelBand et al.
        # loaded. If a node is missing from here, the custom node failed to import.
        try:
            info = httpx.get("http://127.0.0.1:8188/object_info", timeout=10).json()
            needed = ["MelBandRoFormerSampler", "MelBandRoFormerModelLoader",
                     "WanVideoSampler", "MultiTalkWav2VecEmbeds"]
            missing = [n for n in needed if n not in info]
            if missing:
                print(f"!!! Missing custom nodes: {missing}")
            else:
                print("All required custom nodes registered.")
        except Exception as e:
            print(f"(node probe failed: {e})")

        # Step 5: Load workflow template
        with open("/workflow/api_workflow.json") as f:
            self.workflow_template = json.load(f)
        print("Setup complete")

    @modal.fastapi_endpoint(method="POST", label="render")
    def render(self, payload: dict) -> dict:
        """Render one InfiniteTalk clip. Same API as HOSTKEY worker."""
        import requests
        import tempfile

        image_url = payload.get("image_url", "")
        audio_url = payload.get("audio_url") or payload.get("wav_url", "")
        prompt = payload.get("prompt", "A person talking naturally to the camera")
        width = int(payload.get("width", 480))
        height = int(payload.get("height", 848))

        if not image_url or not audio_url:
            return {"error": "image_url and audio_url required"}

        COMFY = "http://127.0.0.1:8188"

        with tempfile.TemporaryDirectory(prefix="infinitetalk_") as tmpdir:
            # 1. Download inputs
            print(f"Downloading image: {image_url[:80]}")
            r = requests.get(image_url, timeout=60, allow_redirects=True)
            r.raise_for_status()
            open(os.path.join(tmpdir, "face.png"), "wb").write(r.content)

            print(f"Downloading audio: {audio_url[:80]}")
            r = requests.get(audio_url, timeout=60, allow_redirects=True)
            r.raise_for_status()
            audio_path = os.path.join(tmpdir, "audio.wav")
            open(audio_path, "wb").write(r.content)

            # Probe the audio for its true duration so we can size the
            # MultiTalk frame count correctly. A static num_frames=400 in the
            # workflow produced fixed ~14.76s outputs regardless of how long
            # the script's TTS audio actually was — leading to truncated voice
            # at end of long blocks and silent padding at end of short ones.
            import subprocess as _sp
            try:
                pr = _sp.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                    capture_output=True, text=True, timeout=30,
                )
                audio_seconds = float(pr.stdout.strip())
            except Exception as e:
                print(f"audio probe failed, falling back to default: {e}")
                audio_seconds = 16.0
            # Bake at 30 fps to match the FFmpeg composer (which composites at
            # 30 fps). Mismatched fps caused subtle time-stretch where each
            # clip's frames played at 25 fps inside a 30 fps composition.
            target_fps = 30
            # Add a small safety pad so the last frames have audio support and
            # InfiniteTalk doesn't truncate the final mouth movement; the
            # ffmpeg composer will trim back to audio length below.
            num_frames = max(int((audio_seconds + 0.2) * target_fps), 24)
            print(f"audio_seconds={audio_seconds:.3f}  num_frames={num_frames}  fps={target_fps}")

            # 2. Upload to ComfyUI input folder
            for fname in ["face.png", "audio.wav"]:
                fpath = os.path.join(tmpdir, fname)
                with open(fpath, "rb") as f:
                    up = requests.post(
                        f"{COMFY}/upload/image",
                        files={"image": (fname, f)},
                        data={"subfolder": "", "type": "input"},
                        timeout=30,
                    )
                    up.raise_for_status()

            # 3. Customize workflow
            wf = copy.deepcopy(self.workflow_template)
            prefix = "infinitetalk_" + uuid.uuid4().hex[:8]
            for nid, node in wf.items():
                ct = node.get("class_type", "")
                if ct == "LoadImage":
                    node["inputs"]["image"] = "face.png"
                elif ct == "LoadAudio":
                    node["inputs"]["audio"] = "audio.wav"
                elif ct == "WanVideoTextEncodeCached" and prompt:
                    node["inputs"]["positive_prompt"] = prompt
                elif ct == "WanVideoImageToVideoMultiTalk":
                    node["inputs"]["width"] = width
                    node["inputs"]["height"] = height
                elif ct == "ImageResizeKJv2":
                    node["inputs"]["width"] = width
                    node["inputs"]["height"] = height
                elif ct == "VHS_VideoCombine":
                    node["inputs"]["filename_prefix"] = prefix
                    # Match composition fps and let the node clip the video
                    # to the audio length so we never overshoot.
                    node["inputs"]["frame_rate"] = target_fps
                    node["inputs"]["trim_to_audio"] = True
                elif ct == "MultiTalkWav2VecEmbeds":
                    # Drive output length from the actual audio duration so
                    # short scripts produce short clips and long scripts produce
                    # long ones — instead of every clip being ~14.76s.
                    node["inputs"]["num_frames"] = num_frames
                    node["inputs"]["fps"] = target_fps

            # 4. Submit. If ComfyUI rejects the workflow with 400, surface the
            # exact error body so we can debug which node/input is invalid.
            # (ComfyUI returns JSON: {"error": {...}, "node_errors": {...}})
            client_id = uuid.uuid4().hex
            resp = requests.post(
                f"{COMFY}/prompt",
                json={"prompt": wf, "client_id": client_id},
                timeout=60,
            )
            if resp.status_code >= 400:
                body = resp.text[:4000]
                print(f"!!! ComfyUI /prompt rejected workflow: HTTP {resp.status_code}")
                print(f"!!! Response body: {body}")
                resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            print(f"Submitted: {prompt_id}")

            # 5. Poll for completion
            start = time.monotonic()
            h = {}
            while time.monotonic() - start < 1700:
                time.sleep(10)
                try:
                    h = requests.get(f"{COMFY}/history/{prompt_id}", timeout=15).json()
                    if prompt_id in h:
                        s = h[prompt_id].get("status", {})
                        if s.get("completed"):
                            break
                        if s.get("status_str") == "error":
                            raise RuntimeError(f"ComfyUI error: {s.get('messages', [])}")
                except requests.RequestException as e:
                    print(f"Poll error: {e}")
            else:
                raise RuntimeError("Timed out waiting for ComfyUI (1700s)")

            # 6. Retrieve output video
            outputs = h[prompt_id].get("outputs", {})
            video_info = None
            for nout in outputs.values():
                for k in ("gifs", "videos"):
                    if k in nout and nout[k]:
                        video_info = nout[k][0]
                        break
                if video_info:
                    break
            if not video_info:
                raise RuntimeError(f"No video output: {list(outputs.keys())}")

            vr = requests.get(
                f"{COMFY}/view",
                params={
                    "filename": video_info["filename"],
                    "subfolder": video_info.get("subfolder", ""),
                    "type": video_info.get("type", "output"),
                },
                timeout=120,
            )
            vr.raise_for_status()
            video_bytes = vr.content
            print(f"Video: {len(video_bytes)} bytes")
            return {
                "video": base64.b64encode(video_bytes).decode(),
                "size_bytes": len(video_bytes),
            }
