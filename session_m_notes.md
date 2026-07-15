# Session M — ACE-Step Investigation Notes

## Date: 2026-04-10

---

## 0.1 Model Architecture

### ACEStepTransformer2DModel
- **Location**: `acestep/models/ace_step_transformer.py`
- **Config**: 24 layers, inner_dim=2560, 20 attention heads × 128 head_dim, mlp_ratio=2.5
- **Input**: latent `hidden_states` [B, 8, 16, T], where T = frame_length = duration * 44100 / 512 / 8
- **Conditioning**: text embeddings, speaker embeddings (512-dim), lyric token IDs, lyric mask, SSL hidden states (optional)
- **Output**: `Transformer2DModelOutput(sample=output, proj_losses=list_of_ssl_losses)`
- **Supports gradient checkpointing**: YES (`_supports_gradient_checkpointing = True`)
- **Inherits**: `PeftAdapterMixin` — native PEFT/LoRA support via `add_adapter()`

### Forward Signature
```python
def forward(
    self,
    hidden_states: torch.Tensor,          # [B, 8, 16, T] latent
    attention_mask: torch.Tensor,          # [B, T]
    encoder_text_hidden_states: torch.Tensor,  # [B, seq, 768] from text encoder
    text_attention_mask: torch.LongTensor,     # [B, seq]
    speaker_embeds: torch.FloatTensor,         # [B, 512]
    lyric_token_idx: torch.LongTensor,         # [B, lyric_len]
    lyric_mask: torch.LongTensor,              # [B, lyric_len]
    timestep: torch.Tensor,                    # [B] float
    ssl_hidden_states: Optional[List[torch.Tensor]] = None,  # List of [list of (T_ssl, D)]
    ...
) -> Transformer2DModelOutput
```

### LoRA Target Modules (actual names from model)
```
transformer_blocks.{0-23}.attn.to_q    (self-attention)
transformer_blocks.{0-23}.attn.to_k
transformer_blocks.{0-23}.attn.to_v
transformer_blocks.{0-23}.attn.to_out.0
transformer_blocks.{0-23}.cross_attn.to_q  (cross-attention)
transformer_blocks.{0-23}.cross_attn.to_k
transformer_blocks.{0-23}.cross_attn.to_v
transformer_blocks.{0-23}.cross_attn.to_out.0
```
Total: 192 linear modules for LoRA

### MusicDCAE (VAE equivalent)
- **Location**: `acestep/music_dcae/music_dcae_pipeline.py`
- **encode(audios, audio_lengths, sr)**: audios [B, 2, T] at 48kHz → resamples to 44100 → mel → DCAE encoder → latents [B, 8, 16, T']
- **scale_factor**: 0.1786
- **shift_factor**: -1.9091
- **Encoding formula**: `latents = (raw_latent - shift_factor) * scale_factor`
- **Padding**: audio padded to multiple of 8*512 = 4096 samples
- **Input requires stereo**: [B, 2, T]. Mono must be duplicated to stereo.

### Text Encoder
- **Model**: UMT5EncoderModel (umt5-base)
- **Tokenizer**: AutoTokenizer
- **Output**: last_hidden_state [B, seq, 768] + attention_mask [B, seq]
- **Max length**: 256 tokens

---

## 0.2 Trainer.py Configuration

### Optimizer
- **Class**: `torch.optim.AdamW`
- **LR**: 1e-4 (default)
- **Betas**: (0.8, 0.9) — NOTE: unusual, not standard (0.9, 0.999)
- **Weight decay**: 1e-2
- **Trainable params**: only LoRA params (base model frozen)

### LR Schedule
- **Type**: LambdaLR with warmup + linear decay
- **Warmup steps**: 10 (default)
- **Warmup**: linear from 0 to LR
- **Decay**: linear from LR to 0 over (max_steps - warmup_steps)

### Gradient Clipping
- **Value**: 0.5
- **Algorithm**: norm (`gradient_clip_algorithm="norm"`)

### Precision
- **Default**: fp32 (trainer.py arg `--precision 32`)
- **Pipeline loads in bf16** for inference
- **For training**: trainer.py uses fp32 by default, but we'll use bf16 autocast for VRAM savings

### Scheduler (Noise)
- **Class**: `FlowMatchEulerDiscreteScheduler`
- **num_train_timesteps**: 1000
- **shift**: 3.0

### Timestep Sampling
- **Type**: logit_normal
- **logit_mean**: 0.0, **logit_std**: 1.0
- Sample u ~ N(0, 1), apply sigmoid, scale by num_train_timesteps → get timestep indices

---

## 0.3 Training Step (run_step) — CRITICAL

### Flow Matching Training Loop (NOT standard DDPM):

1. **Preprocess batch**: encode audio to latents, get text/speaker/lyric embeddings
2. **Generate noise**: `noise = torch.randn_like(target_latents)`
3. **Sample timesteps**: logit-normal distribution
4. **Get sigmas**: from scheduler based on timesteps
5. **Add noise (flow matching)**: `noisy = sigma * noise + (1 - sigma) * target_latents`
6. **Forward pass**: transformer predicts from noisy input
7. **Precondition output**: `model_pred = model_pred * (-sigma) + noisy`
8. **Loss target**: the CLEAN target latents (NOT the noise!)
9. **Loss**: MSE between preconditioned model_pred and target latents, masked by attention_mask
10. **SSL projection losses**: cosine embedding loss between transformer intermediate states and MERT/mHuBERT features

### Key Differences from Standard Diffusion:
- **Flow matching**: target is clean latents, NOT noise
- **Preconditioning**: `model_pred = model_pred * (-sigma) + noisy`
- **Sigma interpolation**: `noisy = sigma * noise + (1 - sigma) * target`
- **Logit-normal timestep sampling** (not uniform)

### SSL Losses (Optional for LoRA)
The SSL losses use MERT-v1-330M and mHuBERT-147 models to compute self-supervised representations of the audio, then project the transformer's intermediate hidden states to match. These are:
- **MERT**: operates at 24kHz, processes 5-second chunks
- **mHuBERT**: operates at 16kHz, processes 30-second chunks
- Combined with denoising loss: `total_loss = denoising_loss + ssl_coeff * avg(ssl_losses)`
- **ssl_coeff**: 1.0 (default)

**Decision**: For LoRA fine-tuning on small datasets (2-10 clips), we SKIP the SSL losses.
- Loading MERT + mHuBERT adds ~2GB VRAM (on top of ~7GB for transformer)
- SSL regularization helps large-scale pretraining but adds complexity for small LoRA runs
- The denoising loss alone is sufficient for style transfer via LoRA

### CFG Dropout (Training)
During training, there's classifier-free guidance dropout:
- Text embeddings: 15% chance of zeroing out
- Speaker embeddings: 50% chance of zeroing out
- Lyrics: 15% chance of zeroing out

We should replicate this for proper LoRA training.

---

## 0.4 Model Loading Test

**Result**: SUCCESS
- Pipeline loads from `/opt/gpu-worker/ace-step-cache` in ~10s
- Components accessible: `pipeline.ace_step_transformer`, `pipeline.music_dcae`, `pipeline.text_encoder_model`, `pipeline.text_tokenizer`
- Transformer on cuda:0, dtype bf16
- 306 attention-related modules available for LoRA targeting

---

## 0.5 xformers Usage

**grep found no xformers imports in ACE-Step code.** The recursion issue was from the broader environment (xformers installed for other models). The recursion preamble in worker.py stays as defense-in-depth.

---

## Implementation Notes for Phase 1

### compute_training_loss pseudocode:
```python
# 1. Prepare audio: mono → stereo [B, 2, T], padded
# 2. Encode audio to latents: dcae.encode(audio, audio_lengths)
# 3. Get text embeddings: text_encoder(prompts) → [B, seq, 768]
# 4. Prepare speaker_embeds: zero vector [B, 512] (no speaker info for instrumental)
# 5. Prepare lyric_token_ids: [0] placeholder, lyric_mask: [0]
# 6. CFG dropout: randomly zero text/speaker/lyrics during training
# 7. Sample timesteps via logit-normal
# 8. Get sigmas from scheduler
# 9. Flow matching: noisy = sigma * noise + (1 - sigma) * target_latents
# 10. Forward: model(noisy, mask, text_emb, text_mask, speaker, lyric_ids, lyric_mask, timestep)
# 11. Precondition: model_pred = model_pred * (-sigma) + noisy
# 12. Masked MSE loss: only over non-padded regions
```

### VRAM Budget (RTX 4090, 24GB):
- Transformer (bf16): ~7GB
- DCAE (bf16): ~2GB
- Text encoder (bf16): ~1GB
- Optimizer states (LoRA only): ~0.5GB
- Activations + gradients: ~3-5GB
- **Total estimated**: ~14-16GB — should fit with gradient checkpointing
- If OOM: enable gradient checkpointing on transformer

### LoRA Config:
- rank: 16 (default for endpoint, 64 was old default — too large)
- alpha: rank // 4 = 4
- target_modules: ["to_q", "to_k", "to_v", "to_out.0"]
- use_rslora: True
- dropout: 0.0
