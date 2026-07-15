"""RunPod Serverless handler for advanced audio processing.

Pipeline:
1. Download video(s) via yt-dlp, extract audio
2. Vocal isolation via BS-RoFormer (audio-separator)
3. Silence trimming, high-pass filter, loudnorm normalization
4. Select best 60-90s segment of dense speech
5. Transcribe with Whisper
6. Upload clean vocals + transcript to R2
"""
import os
import subprocess
import shutil
import tempfile

import boto3
import numpy as np
import requests
import runpod

# R2 configuration
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET", "luminacast")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "https://media.luminacast.com")


def get_s3():
    return boto3.client("s3", endpoint_url=R2_ENDPOINT,
                        aws_access_key_id=R2_ACCESS_KEY,
                        aws_secret_access_key=R2_SECRET_KEY,
                        region_name="auto")


def download_audio(url: str, work_dir: str) -> str:
    """Download video via yt-dlp and extract audio as WAV."""
    video_path = os.path.join(work_dir, "input_video.mp4")
    audio_path = os.path.join(work_dir, "input_audio.wav")

    if "tiktok.com" in url:
        subprocess.run(
            ["yt-dlp", "-f", "mp4/best[ext=mp4]/best", "--no-playlist",
             "--max-filesize", "50M", "-o", video_path,
             "--no-warnings", "--quiet", url],
            capture_output=True, timeout=60)
    else:
        resp = requests.get(url, timeout=120)
        with open(video_path if not url.endswith(".wav") else audio_path, "wb") as f:
            f.write(resp.content)
        if url.endswith(".wav") or url.endswith(".mp3"):
            return audio_path

    # Extract audio as mono 44.1kHz WAV
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn",
         "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", audio_path],
        capture_output=True, timeout=30)
    return audio_path


def isolate_vocals(audio_path: str, work_dir: str) -> str:
    """Isolate vocals using BS-RoFormer via audio-separator."""
    from audio_separator.separator import Separator

    output_dir = os.path.join(work_dir, "separated")
    os.makedirs(output_dir, exist_ok=True)

    separator = Separator(output_dir=output_dir)
    separator.load_model("model_bs_roformer_ep_317_sdr_12.9755.ckpt")
    output_files = separator.separate(audio_path)

    # Find vocals file
    vocals_path = None
    for f in output_files:
        if "vocal" in f.lower() or "voice" in f.lower():
            vocals_path = f
            break
    if not vocals_path and output_files:
        vocals_path = output_files[0]

    if not vocals_path or not os.path.exists(vocals_path):
        print("BS-RoFormer did not produce vocals, using original audio")
        return audio_path

    print(f"Vocals isolated: {os.path.getsize(vocals_path)} bytes")
    return vocals_path


def process_audio(vocals_path: str, work_dir: str, max_duration: int = 90) -> str:
    """Trim silence, high-pass filter, normalize loudness, select best segment."""

    # Step 1: Silence removal + high-pass filter + loudnorm
    processed_path = os.path.join(work_dir, "processed.wav")
    ffmpeg_filter = (
        # High-pass at 80Hz (removes rumble)
        "highpass=f=80,"
        # Low-pass at 14kHz (removes hiss/artifacts above speech range)
        "lowpass=f=14000,"
        # Aggressive silence removal
        "silenceremove=start_periods=1:start_silence=0.3:start_threshold=-35dB:"
        "stop_periods=-1:stop_silence=0.3:stop_threshold=-35dB,"
        # Loudness normalization to -16 LUFS
        "loudnorm=I=-16:LRA=11:TP=-1.5"
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", vocals_path, "-af", ffmpeg_filter,
         "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
         processed_path],
        capture_output=True, timeout=60)

    if result.returncode != 0 or not os.path.exists(processed_path):
        print(f"Audio processing failed: {result.stderr.decode()[:200]}")
        return vocals_path

    # Step 2: Select best 60-90s segment (densest speech)
    final_path = os.path.join(work_dir, "final.wav")
    file_size = os.path.getsize(processed_path)
    duration_s = file_size / (44100 * 2)  # 16-bit mono

    if duration_s <= max_duration:
        # Already short enough
        return processed_path

    # Find the densest speech segment using energy analysis
    try:
        import wave
        with wave.open(processed_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32)

        # Calculate energy in 1-second windows
        sample_rate = 44100
        window_size = sample_rate  # 1 second
        n_windows = len(audio_data) // window_size
        energies = []
        for i in range(n_windows):
            chunk = audio_data[i * window_size:(i + 1) * window_size]
            energies.append(np.sqrt(np.mean(chunk ** 2)))

        # Find the best starting point for max_duration seconds
        target_windows = min(max_duration, n_windows)
        best_start = 0
        best_energy = 0
        for start in range(n_windows - target_windows + 1):
            total_energy = sum(energies[start:start + target_windows])
            if total_energy > best_energy:
                best_energy = total_energy
                best_start = start

        start_time = best_start
        print(f"Selected segment: {start_time}s to {start_time + target_windows}s (total {duration_s:.0f}s)")

        subprocess.run(
            ["ffmpeg", "-y", "-i", processed_path,
             "-ss", str(start_time), "-t", str(target_windows),
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
             final_path],
            capture_output=True, timeout=30)

        if os.path.exists(final_path) and os.path.getsize(final_path) > 1000:
            return final_path
    except Exception as e:
        print(f"Segment selection failed: {e}")

    # Fallback: just take first max_duration seconds
    subprocess.run(
        ["ffmpeg", "-y", "-i", processed_path,
         "-t", str(max_duration),
         "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
         final_path],
        capture_output=True, timeout=30)
    return final_path if os.path.exists(final_path) else processed_path


def transcribe(audio_path: str) -> str:
    """Transcribe audio using Whisper large-v3 model (runs on RunPod GPU)."""
    try:
        import whisper
        model = whisper.load_model("large-v3")
        result = model.transcribe(audio_path, language="en")
        text = result.get("text", "").strip()
        print(f"Transcription ({len(text)} chars): {text[:200]}...")
        return text
    except Exception as e:
        print(f"Transcription failed: {e}")
        return ""


def handler(event):
    """RunPod handler.

    Input:
        audio_url: str — URL of video/audio to process
        output_key: str — R2 key for output
        max_duration: int — max seconds (default 90)

    Output:
        vocals_url: str — CDN URL of clean vocals
        transcript: str — Whisper transcription
        duration_seconds: float
    """
    job_input = event.get("input", {})
    audio_url = job_input.get("audio_url")
    output_key = job_input.get("output_key", "audio_worker/output.wav")
    max_duration = job_input.get("max_duration", 90)

    if not audio_url:
        return {"error": "audio_url is required"}

    work_dir = tempfile.mkdtemp(prefix="audio_worker_")

    try:
        # 1. Download and extract audio
        print(f"Downloading: {audio_url[:80]}...")
        audio_path = download_audio(audio_url, work_dir)
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            return {"error": "Failed to download/extract audio"}
        print(f"Audio extracted: {os.path.getsize(audio_path)} bytes")

        # 2. Vocal isolation (BS-RoFormer)
        print("Isolating vocals...")
        vocals_path = isolate_vocals(audio_path, work_dir)

        # 3. Process: silence trim, high-pass, loudnorm, segment selection
        print("Processing audio...")
        final_path = process_audio(vocals_path, work_dir, max_duration)
        final_size = os.path.getsize(final_path)
        duration = final_size / (44100 * 2)
        print(f"Final audio: {final_size} bytes, ~{duration:.1f}s")

        # 4. Transcribe
        print("Transcribing...")
        transcript = transcribe(final_path)

        # 5. Upload to R2
        print(f"Uploading to R2: {output_key}")
        s3 = get_s3()
        s3.upload_file(final_path, R2_BUCKET, output_key,
                       ExtraArgs={"ContentType": "audio/wav"})
        vocals_url = f"{R2_PUBLIC_URL}/{output_key}"

        return {
            "vocals_url": vocals_url,
            "transcript": transcript,
            "duration_seconds": round(duration, 1),
            "vocals_size": final_size,
        }

    except Exception as e:
        print(f"Handler error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

    finally:
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass


runpod.serverless.start({"handler": handler})
