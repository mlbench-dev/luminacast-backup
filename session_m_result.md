# Session M Results — Music Training via Raw PyTorch Loop

## Date: 2026-04-10

## Summary
Successfully replaced the broken PyTorch Lightning music training pipeline with a clean,
single-process raw PyTorch training loop. All phases completed.

## Phase Results

### Phase -1: MUSETALK verification
- MUSETALK_AVAILABLE=true already set on VPS
- GPU worker healthy with ace_step=true, musetalk_installed=true

### Phase 0: Investigation
- ACE-Step uses **flow matching** (not standard DDPM/DDIM)
- Transformer: ACEStepTransformer2DModel, 24 layers, 2560 inner dim, 20 heads
- DCAE: MusicDCAE, scale_factor=0.1786, shift_factor=-1.9091
- Optimizer: AdamW, betas=(0.8, 0.9), weight_decay=0.01, LR=1e-4
- LR schedule: warmup 10 steps + linear decay
- Gradient clipping: 0.5 norm
- Loss: flow matching MSE on clean target (not noise prediction)
- Findings saved to session_m_notes.md

### Phase 1: Raw trainer script
- ace_step_raw_trainer.py created and tested standalone
- 5 steps: 17s, peak VRAM 15.6 GB
- 10 steps: 20s, rank-16 LoRA = 63MB, 384 tensors

### Phase 2: Worker.py integration
- Old PL endpoint replaced with raw trainer subprocess call
- Added json and JSONResponse imports
- Worker restarts cleanly, health OK

### Phase 3: End-to-end testing
- 10-step (real Thomas Newman audio): 36s, final_loss=0.032, mean_loss=0.045
- 100-step: 91s, final_loss=0.026, mean_loss=0.048
- LoRA in R2: 62.97 MB, verified loadable (384 tensors, 15.7M params)

### Phase 4: Wrap
- Journey test created: journey-music-training.spec.ts
- STATUS.md updated, KNOWN_BROKEN marker removed
- All files committed

## Training Timing
| Steps | Total Time | Per-Step | Notes |
|-------|-----------|----------|-------|
| 5     | 17s       | ~0.4s    | Standalone, synthetic audio |
| 10    | 20s       | ~0.4s    | Standalone, rank-16 |
| 10    | 36s       | ~0.4s    | E2E (includes download+upload) |
| 100   | 91s       | ~0.5s    | E2E, real audio from R2 |

## LoRA File Verification
```
Tensors: 384
LoRA A tensors: 192
LoRA B tensors: 192
Total params: 15,728,640
File size: 62,967,264 bytes (~63 MB)
Sample key: base_model.model.transformer_blocks.0.attn.to_k.lora_A.weight
Shape: [16, 2560] (rank 16 × inner_dim 2560)
```

## Key Architectural Decisions
1. **Skip SSL losses** (MERT + mHuBERT): saves ~2GB VRAM, not needed for LoRA fine-tuning
2. **fp32 training for DCAE encode**: mel transform requires float32 filter bank
3. **fp32 for transformer training**: matches trainer.py default, avoids NaN issues
4. **Gradient checkpointing enabled**: reduces VRAM from ~20GB to ~16GB
5. **num_workers=0**: avoids subprocess forking entirely
6. **ffmpeg for audio loading**: avoids torchaudio/torchcodec dependency
