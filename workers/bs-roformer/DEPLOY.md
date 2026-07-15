# BS-RoFormer Vocal Isolation — RunPod Deployment Guide

## What This Does
Isolates vocals from background music/noise in TikTok audio before sending to Fish Audio for voice cloning. Uses BS-RoFormer (BS-Roformer-ViperX-1297.ckpt model via audio-separator) for high-quality source separation.

## Step-by-Step Deployment

### 1. Build and Push Docker Image

```bash
# From the luminacast-omni repo root:
cd workers/bs-roformer

# Build the image
docker build -t bs-roformer-worker .

# Tag for Docker Hub (or any registry RunPod can pull from)
docker tag bs-roformer-worker YOUR_DOCKERHUB_USER/bs-roformer-worker:latest

# Push
docker push YOUR_DOCKERHUB_USER/bs-roformer-worker:latest
```

### 2. Create RunPod Serverless Endpoint

1. Go to [RunPod Console](https://www.runpod.io/console/serverless)
2. Click **"+ New Endpoint"**
3. Configure:
   - **Name**: `bs_roformer_vocal_isolation`
   - **Docker Image**: `YOUR_DOCKERHUB_USER/bs-roformer-worker:latest`
   - **GPU**: Not required (CPU-only works, but GPU is 5-10x faster)
     - For CPU: select a CPU worker type
     - For GPU: select the cheapest GPU (e.g., RTX 3060 or similar)
   - **Min Workers**: 0 (scale to zero when idle)
   - **Max Workers**: 2
   - **Idle Timeout**: 5 seconds
   - **Execution Timeout**: 600 seconds (10 minutes)

4. **Environment Variables** (add these in the RunPod dashboard):
   ```
   R2_ENDPOINT=https://0adcb995bfca0f652d6b86f053a6de8f.r2.cloudflarestorage.com
   R2_ACCESS_KEY=b728d1ef06a0f1bdb4954080bcf3052d
   R2_SECRET_KEY=<your R2 secret key>
   R2_BUCKET=luminacast
   R2_PUBLIC_URL=https://media.luminacast.com
   ```

5. Click **"Create"**

### 3. Get the Endpoint ID

After creation, you'll see the endpoint in your dashboard. The endpoint ID looks like: `abc123xyz`

The API URL will be: `https://api.runpod.ai/v2/abc123xyz/run`

### 4. Test the Endpoint

```bash
curl -X POST "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/run" \
  -H "Authorization: Bearer YOUR_RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "audio_url": "https://media.luminacast.com/some/audio.wav",
      "output_key": "bs-roformer/test_output.wav"
    }
  }'
```

### 5. Configure in Luminacast

Add to your `.env` or `config.py`:
```
BS_ROFORMER_ENDPOINT_ID=YOUR_ENDPOINT_ID
```

Then in the pipeline, before sending to Fish Audio:
1. Upload raw WAV to R2
2. Submit to BS-RoFormer vocal isolation endpoint with the R2 URL
3. Poll for completion
4. Use the returned vocals_url for Fish Audio clone

## Architecture Flow

```
TikTok Videos (×3) → yt-dlp download → ffmpeg extract audio
    → ffmpeg concat + silence trim → clean WAV (≤120s)
    → Upload to R2 → Submit to BS-RoFormer vocal isolation RunPod
    → BS-RoFormer isolates vocals → Upload vocals to R2
    → Download vocals → Fish Audio clone_voice (multipart)
    → voice_id → TTS with cloned voice
```

## Cost Estimate
- CPU worker: ~$0.01-0.03 per audio file (1-3 min processing)
- GPU worker: ~$0.02-0.05 per audio file (10-30 sec processing)
- Scale-to-zero means no cost when idle

## Troubleshooting
- If BS-RoFormer vocal isolation OOMs: use `--segment` flag to process in chunks
- If audio is too long: the pipeline already caps at 120s
- If Docker build is too large: the BS-Roformer-ViperX-1297.ckpt model is ~80MB, total image ~3GB
