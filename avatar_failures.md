# Avatar Failure Root Cause Analysis — 2026-04-13

## Summary

All 57 failed avatars have `progress_step = "Generation timed out — please retry"`. The root cause is GPU memory exhaustion on the GPU worker server.

## Root Cause Chain

1. **Ace-Step music generation process (PID 558540) holds 16,572 MiB of GPU memory** on the RTX 4090 (24,564 MiB total). This leaves only ~8 GB free.

2. **GPU worker HTTP server (PID 552087, uvicorn on port 7860) is running but unresponsive** — `curl localhost:7860/health` times out. The server accepted the TCP connection but never returns a response, indicating the FastAPI event loop is blocked or model loading hangs due to insufficient GPU memory.

3. **Orchestrator LoRA training requests to `http://194.247.183.12:7860/api/...` fail** with:
   - HTTP 500 "LoRA training failed" (15x in last 24h)
   - Consecutive HTTP errors (6x)
   - KeyError: 'lora_r2_key' (6x — secondary error from missing training output)

4. **GPU worker restart attempts fail** with port bind error (4,513x in Sentry):
   - `[Errno 98] error while attempting to bind on address ('0.0.0.0', 7860): address already in use`
   - The old hung process still holds the port

5. **Avatar generation timeout** — orchestrator polls for completion, eventually times out, marks avatar FAILED with "Generation timed out — please retry"

## Evidence

```
# GPU server state at 2026-04-13 09:48 UTC
nvidia-smi output:
  GPU 0: RTX 4090, 87°C, 350W/450W, 16582/24564 MiB used, 45% utilization
  Process: PID 558540, ace-step/venv_ace/bin/python3, 16572 MiB

# GPU worker state:
  PID 552087, uvicorn on port 7860, running since 00:44 UTC
  Health endpoint: TIMEOUT (not responding)
  Port 7860: LISTEN (socket bound but not serving)

# Sentry top errors:
  LUMINACAST-GPU-WORKER-3: port 7860 bind failure (4,513x)
  LUMINACAST-GPU-WORKER-7: Training failed (13x)
  LUMINACAST-GPU-WORKER-4: 'torch._C._CudaDeviceProperties' no attribute 'total_mem' (9x)
```

## Fix Required (infrastructure, not code)

The GPU worker needs the ace-step process stopped when not actively generating music, or the ace-step model needs to share GPU memory via proper model hot-swapping (the worker.py already has asyncio.Lock() for this, but ace-step runs in a separate venv/process).

**Immediate fix**: Kill the ace-step process, restart GPU worker:
```bash
ssh root@194.247.183.12
kill 558540  # ace-step python process
kill 552087  # hung uvicorn
sleep 2
cd /opt/gpu-worker && ./start.sh
```

**Long-term fix**: Integrate ace-step into the unified GPU worker's model hot-swap system so models share the GPU memory pool behind the asyncio.Lock().

## Impact

- 57 avatars marked FAILED — all with "Generation timed out — please retry"
- All avatar creation attempts will fail until GPU worker is restarted
- Music generation (ace-step) may also be affected once memory is reclaimed

## Note

Per the spec, this analysis does NOT touch avatar pipeline code. This is a GPU server infrastructure issue requiring a process restart.
