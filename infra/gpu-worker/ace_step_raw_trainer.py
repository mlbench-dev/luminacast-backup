#!/usr/bin/env python3
"""
ACE-Step raw PyTorch training loop for Luminacast music LoRA.

Bypasses PyTorch Lightning to avoid subprocess forking + xformers monkey-patch
recursion issues. Matches ACE-Step's trainer.py config exactly — see
session_m_notes.md for the reference.

Usage:
    python ace_step_raw_trainer.py \
        --audio_paths /path/to/1.wav /path/to/2.wav \
        --prompts "upbeat electronic" "acoustic lofi" \
        --output_dir /path/to/output \
        --max_steps 10 \
        --learning_rate 1e-4 \
        --lora_rank 16

Outputs:
    {output_dir}/lora.safetensors — the trained LoRA weights
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW

# Add ACE-Step to path
sys.path.insert(0, "/opt/gpu-worker/ace-step")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_audio_to_tensor(audio_path: str, target_sr: int = 48000) -> torch.Tensor:
    """Load audio via ffmpeg subprocess — no torchaudio, no torchcodec.
    Returns stereo tensor [2, T] at target_sr."""
    cmd = [
        "ffmpeg", "-v", "quiet",
        "-i", audio_path,
        "-f", "f32le",           # 32-bit float PCM
        "-ac", "2",              # stereo (ACE-Step DCAE expects stereo)
        "-ar", str(target_sr),
        "-"                      # output to stdout
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    audio = np.frombuffer(result.stdout, dtype=np.float32).copy()
    # Reshape to [T, 2] then transpose to [2, T]
    audio = audio.reshape(-1, 2).T
    audio = torch.from_numpy(audio).float()
    # Clamp to [-1, 1]
    audio = torch.clamp(audio, -1.0, 1.0)
    # Pad to minimum 3 seconds
    min_samples = target_sr * 3
    if audio.shape[-1] < min_samples:
        audio = F.pad(audio, (0, min_samples - audio.shape[-1]))
    return audio


class SimpleMusicDataset(Dataset):
    """Direct PyTorch dataset. No HuggingFace Arrow. No retries."""

    def __init__(self, audio_paths: list, prompts: list, target_sr: int = 48000):
        assert len(audio_paths) == len(prompts), "audio_paths and prompts must match length"
        self.audio_paths = audio_paths
        self.prompts = prompts
        self.target_sr = target_sr

        # Pre-load all audio to avoid repeated disk I/O (dataset is tiny)
        logger.info("Loading %d audio files into memory...", len(audio_paths))
        self.audio_tensors = []
        for i, path in enumerate(audio_paths):
            audio_tensor = load_audio_to_tensor(path, target_sr)
            self.audio_tensors.append(audio_tensor)
            dur = audio_tensor.shape[-1] / target_sr
            logger.info("  [%d] %s: shape=%s (%.1fs)", i, path, audio_tensor.shape, dur)

    def __len__(self):
        return len(self.audio_paths)

    def __getitem__(self, idx):
        return {
            "audio": self.audio_tensors[idx],
            "prompt": self.prompts[idx],
            "idx": idx,
        }


def collate_batch(batch):
    """Collate function. Pads audio to the longest in the batch.
    Audio is [2, T] per item → padded to [B, 2, max_T]."""
    max_len = max(item["audio"].shape[-1] for item in batch)
    # Pad to multiple of 8*512=4096 for DCAE
    pad_multiple = 8 * 512
    if max_len % pad_multiple != 0:
        max_len = max_len + (pad_multiple - max_len % pad_multiple)

    audio_batch = torch.zeros(len(batch), 2, max_len)
    wav_lengths = []
    for i, item in enumerate(batch):
        t = item["audio"].shape[-1]
        audio_batch[i, :, :t] = item["audio"]
        wav_lengths.append(t)

    return {
        "audio": audio_batch,
        "wav_lengths": torch.LongTensor(wav_lengths),
        "prompts": [item["prompt"] for item in batch],
    }


def get_timestep_logit_normal(bsz, scheduler, device, logit_mean=0.0, logit_std=1.0):
    """Sample timesteps using logit-normal distribution, matching trainer.py."""
    u = torch.normal(mean=logit_mean, std=logit_std, size=(bsz,), device="cpu")
    u = torch.nn.functional.sigmoid(u)
    indices = (u * scheduler.config.num_train_timesteps).long()
    indices = torch.clamp(indices, 0, scheduler.config.num_train_timesteps - 1)
    timesteps = scheduler.timesteps[indices].to(device)
    return timesteps


def get_sd3_sigmas(scheduler, timesteps, device, n_dim=4, dtype=torch.float32):
    """Get sigmas for given timesteps, matching trainer.py."""
    sigmas = scheduler.sigmas.to(device=device, dtype=dtype)
    schedule_timesteps = scheduler.timesteps.to(device)
    step_indices = [(schedule_timesteps == t).nonzero().item() for t in timesteps]
    sigma = sigmas[step_indices].flatten()
    while len(sigma.shape) < n_dim:
        sigma = sigma.unsqueeze(-1)
    return sigma


def compute_training_loss(model, batch, pipeline, scheduler, device, dtype):
    """
    Single training step using flow matching, matching ACE-Step's trainer.py exactly.

    Flow matching loss (NOT standard DDPM):
    1. Encode audio → latents via DCAE
    2. Encode prompts via text encoder
    3. Sample timesteps (logit-normal)
    4. Compute sigmas and add noise: noisy = sigma * noise + (1-sigma) * target
    5. Predict with transformer
    6. Precondition: pred = pred * (-sigma) + noisy
    7. MSE loss between preconditioned pred and target latents (clean)
    """
    audio = batch["audio"].to(device)  # [B, 2, T]
    wav_lengths = batch["wav_lengths"].to(device)
    prompts = batch["prompts"]
    bs = audio.shape[0]

    # 1. Encode audio to latents via DCAE (no grad needed)
    with torch.no_grad():
        # DCAE encode: mel transform needs float32 input, then internal encoder uses dtype
        # Pass audio as float32 to avoid mel_scale bf16 error
        target_latents, latent_lengths = pipeline.music_dcae.encode(
            audio.float(), wav_lengths, sr=48000
        )
        target_latents = target_latents.to(dtype)
        # target_latents: [B, 8, 16, T']

    # 2. Encode text prompts (no grad needed)
    with torch.no_grad():
        encoder_text_hidden_states, text_attention_mask = pipeline.get_text_embeddings(
            prompts
        )
        encoder_text_hidden_states = encoder_text_hidden_states.to(dtype)

    # 3. Prepare conditioning (speaker, lyrics)
    speaker_embeds = torch.zeros(bs, 512, device=device, dtype=dtype)
    lyric_token_ids = torch.zeros(bs, 1, device=device, dtype=torch.long)
    lyric_mask = torch.zeros(bs, 1, device=device, dtype=dtype)

    # 4. CFG dropout (matching trainer.py preprocess)
    # Text: 15% chance of zeroing
    text_cfg_mask = torch.where(
        torch.rand(bs, device=device) < 0.15,
        torch.zeros(bs, device=device),
        torch.ones(bs, device=device),
    ).long()
    encoder_text_hidden_states = torch.where(
        text_cfg_mask.unsqueeze(1).unsqueeze(1).bool(),
        encoder_text_hidden_states,
        torch.zeros_like(encoder_text_hidden_states),
    )

    # Speaker: 50% chance of zeroing
    spk_cfg_mask = torch.where(
        torch.rand(bs, device=device) < 0.50,
        torch.zeros(bs, device=device),
        torch.ones(bs, device=device),
    ).long()
    speaker_embeds = torch.where(
        spk_cfg_mask.unsqueeze(1).bool(),
        speaker_embeds,
        torch.zeros_like(speaker_embeds),
    )

    # Lyrics: 15% chance of zeroing (already zeros, but keep for correctness)
    lyric_cfg_mask = torch.where(
        torch.rand(bs, device=device) < 0.15,
        torch.zeros(bs, device=device),
        torch.ones(bs, device=device),
    ).long()
    lyric_token_ids = torch.where(
        lyric_cfg_mask.unsqueeze(1).bool(),
        lyric_token_ids,
        torch.zeros_like(lyric_token_ids),
    )
    lyric_mask = torch.where(
        lyric_cfg_mask.unsqueeze(1).bool(),
        lyric_mask,
        torch.zeros_like(lyric_mask),
    )

    # 5. Attention mask for latent time dimension
    attention_mask = torch.ones(bs, target_latents.shape[-1], device=device, dtype=dtype)

    # 6. Sample timesteps and noise (flow matching)
    noise = torch.randn_like(target_latents, device=device)
    timesteps = get_timestep_logit_normal(bs, scheduler, device)

    # 7. Get sigmas and create noisy latents
    sigmas = get_sd3_sigmas(
        scheduler, timesteps, device, n_dim=target_latents.ndim, dtype=dtype
    )
    noisy_latents = sigmas * noise + (1.0 - sigmas) * target_latents

    # 8. Forward pass through transformer
    transformer_output = model(
        hidden_states=noisy_latents,
        attention_mask=attention_mask,
        encoder_text_hidden_states=encoder_text_hidden_states,
        text_attention_mask=text_attention_mask,
        speaker_embeds=speaker_embeds,
        lyric_token_idx=lyric_token_ids,
        lyric_mask=lyric_mask,
        timestep=timesteps.to(device).to(dtype),
        ssl_hidden_states=None,  # Skip SSL losses for LoRA training
    )
    model_pred = transformer_output.sample

    # 9. Precondition output (flow matching): pred = pred * (-sigma) + noisy
    model_pred = model_pred * (-sigmas) + noisy_latents

    # 10. Compute masked MSE loss (target = clean latents)
    target = target_latents
    mask = (
        attention_mask.unsqueeze(1)
        .unsqueeze(1)
        .expand(-1, target.shape[1], target.shape[2], -1)
    )
    selected_pred = (model_pred * mask).reshape(bs, -1).contiguous()
    selected_target = (target * mask).reshape(bs, -1).contiguous()

    loss = F.mse_loss(selected_pred, selected_target, reduction="none")
    loss = loss.mean(1)
    loss = loss * mask.reshape(bs, -1).mean(1)
    loss = loss.mean()

    return loss


def train_one_run(args):
    """Main training entry point."""
    start_time = time.time()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise RuntimeError("CUDA required for training")

    logger.info("Device: %s (%s)", device, torch.cuda.get_device_name(0))
    logger.info("VRAM: %.1f GB", torch.cuda.get_device_properties(0).total_memory / 1e9)

    # Load ACE-Step pipeline (for DCAE, text encoder access)
    logger.info("Loading ACE-Step pipeline from: %s", args.checkpoint_dir)
    from acestep.pipeline_ace_step import ACEStepPipeline

    pipeline = ACEStepPipeline(
        checkpoint_dir=args.checkpoint_dir,
        dtype="bfloat16",
        cpu_offload=False,
    )
    pipeline.load_checkpoint(pipeline.checkpoint_dir)
    dtype = pipeline.dtype  # bfloat16

    # Get the transformer
    model = pipeline.ace_step_transformer
    model = model.to(torch.bfloat16).to(device)  # bf16 weights for VRAM savings
    dtype = torch.bfloat16
    # Mixed precision: bf16 forward pass, fp32 gradient accumulation for quality
    # This gives near-fp32 quality with bf16 memory usage
    use_amp = True
    scaler = torch.amp.GradScaler('cuda', enabled=use_amp)
    logger.info("Mixed precision training: bf16 forward + fp32 gradients (best quality)")

    # Enable gradient checkpointing for VRAM savings
    model.enable_gradient_checkpointing()
    logger.info("Gradient checkpointing enabled")

    # Freeze base model
    for p in model.parameters():
        p.requires_grad = False

    # Apply LoRA via PEFT
    from peft import LoraConfig, get_peft_model

    lora_config = LoraConfig(
        r=args.lora_rank,
        lora_alpha=args.lora_rank // 4 if args.lora_rank >= 4 else args.lora_rank,
        target_modules=args.lora_target_modules,
        lora_dropout=0.0,
        use_rslora=True,
        bias="none",
    )

    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    logger.info("LoRA applied: %d trainable / %d total (%.2f%%)",
                trainable, total, 100 * trainable / total)

    model.to(device)
    model.train()

    # Move DCAE to device in float32 (mel transform requires float32 internally)
    pipeline.music_dcae.to(device).float()
    # Text encoder can stay in bf16 for memory savings
    pipeline.text_encoder_model.to(device).to(torch.bfloat16)

    # Create scheduler (matching trainer.py)
    from acestep.schedulers.scheduling_flow_match_euler_discrete import (
        FlowMatchEulerDiscreteScheduler,
    )
    scheduler = FlowMatchEulerDiscreteScheduler(
        num_train_timesteps=1000,
        shift=3.0,
    )

    # Dataset and dataloader (num_workers=0 — NO FORK)
    dataset = SimpleMusicDataset(args.audio_paths, args.prompts, target_sr=48000)
    dataloader = DataLoader(
        dataset,
        batch_size=1,
        shuffle=True,
        num_workers=0,  # critical: no fork
        collate_fn=collate_batch,
    )

    # Optimizer — match ACE-Step's AdamW config
    optimizer = AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=args.learning_rate,
        betas=(0.8, 0.9),       # ACE-Step uses (0.8, 0.9)
        weight_decay=0.01,
        eps=1e-8,
    )

    # LR scheduler — warmup + linear decay (matching trainer.py)
    warmup_steps = 10
    max_steps = args.max_steps

    def lr_lambda(current_step):
        if current_step < warmup_steps:
            return float(current_step) / float(max(1, warmup_steps))
        else:
            progress = float(current_step - warmup_steps) / float(
                max(1, max_steps - warmup_steps)
            )
            return max(0.0, 1.0 - progress)

    lr_scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    # Training loop
    step = 0
    epoch = 0
    losses = []

    # Pre-encode all audio latents and prompts, then offload DCAE + text encoder to CPU
    # This frees ~4-6GB VRAM before the training loop

    # ═══ Pre-compute all latents + text embeddings ═══
    # This prevents DCAE and text encoder from accumulating GPU memory over 2400 steps
    logger.info("Pre-computing latents and text embeddings for %d samples...", len(dataset))
    precomputed = []
    with torch.no_grad():
        for idx in range(len(dataset)):
            item = dataset[idx]
            audio = item["audio"].unsqueeze(0).to(device)  # [1, 2, T]
            wav_len = torch.tensor([audio.shape[-1]], device=device)
            prompt = item["prompt"]  # single string
            target_latents, latent_lengths = pipeline.music_dcae.encode(
                audio.float(), wav_len, sr=48000
            )
            target_latents = target_latents.to(dtype)

            text_emb, text_mask = pipeline.get_text_embeddings([prompt])
            text_emb = text_emb.to(dtype)

            precomputed.append({
                "target_latents": target_latents.cpu(),
                "latent_lengths": latent_lengths.cpu(),
                "text_emb": text_emb.cpu(),
                "text_mask": text_mask.cpu(),
            })
            logger.info("  Pre-computed [%d/%d] latent=%s", idx + 1, len(dataset), target_latents.shape)

    # Offload DCAE + text encoder to CPU — free ~4-6GB VRAM
    pipeline.music_dcae.to("cpu")
    if hasattr(pipeline, 'text_encoder_model'):
        pipeline.text_encoder_model.to("cpu")
    # pipeline offloaded in pre-compute block (DCAE + text encoder on CPU)
    torch.cuda.empty_cache()
    vram_after = torch.cuda.memory_allocated() / 1e9
    logger.info("DCAE + text encoder offloaded. VRAM: %.1f GB (transformer + LoRA only)", vram_after)

    logger.info("Starting training: max_steps=%d, lr=%g, rank=%d, batch_size=1",
                args.max_steps, args.learning_rate, args.lora_rank)

    vram_logged = False
    import random
    num_samples = len(precomputed)
    while step < args.max_steps:
        epoch += 1
        indices = list(range(num_samples))
        random.shuffle(indices)
        for sample_idx in indices:
            if step >= args.max_steps:
                break

            optimizer.zero_grad()

            # Load precomputed data from CPU → GPU
            pc = precomputed[sample_idx]
            target_latents = pc["target_latents"].to(device)
            text_emb = pc["text_emb"].to(device)
            text_mask = pc["text_mask"].to(device)
            bs = 1

            # Conditioning (zeros for LoRA training)
            speaker_embeds = torch.zeros(bs, 512, device=device, dtype=dtype)
            lyric_token_ids = torch.zeros(bs, 1, device=device, dtype=torch.long)
            lyric_mask = torch.zeros(bs, 1, device=device, dtype=dtype)

            # CFG dropout
            if random.random() < 0.15:
                text_emb = torch.zeros_like(text_emb)
            if random.random() < 0.50:
                speaker_embeds = torch.zeros_like(speaker_embeds)

            # Attention mask
            attention_mask = torch.ones(bs, target_latents.shape[-1], device=device, dtype=dtype)

            # Flow matching: noise + timesteps + sigma interpolation
            noise = torch.randn_like(target_latents)
            timesteps = get_timestep_logit_normal(bs, scheduler, device)
            sigmas = get_sd3_sigmas(scheduler, timesteps, device, n_dim=target_latents.ndim, dtype=dtype)
            noisy_latents = sigmas * noise + (1.0 - sigmas) * target_latents

            # Forward pass
            transformer_output = model(
                hidden_states=noisy_latents,
                attention_mask=attention_mask,
                encoder_text_hidden_states=text_emb,
                text_attention_mask=text_mask,
                speaker_embeds=speaker_embeds,
                lyric_token_idx=lyric_token_ids,
                lyric_mask=lyric_mask,
                timestep=timesteps.to(device).to(dtype),
                ssl_hidden_states=None,
            )
            model_pred = transformer_output.sample
            model_pred = model_pred * (-sigmas) + noisy_latents

            # Masked MSE loss
            target = target_latents
            mask = attention_mask.unsqueeze(1).unsqueeze(1).expand(-1, target.shape[1], target.shape[2], -1)
            selected_pred = (model_pred * mask).reshape(bs, -1).contiguous()
            selected_target = (target * mask).reshape(bs, -1).contiguous()
            loss = torch.nn.functional.mse_loss(selected_pred, selected_target, reduction="mean")

            # Mixed precision backward + optimizer step
            scaler.scale(loss).backward()

            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad],
                max_norm=0.5,
            )

            scaler.step(optimizer)
            scaler.update()
            lr_scheduler.step()

            loss_val = loss.item()
            losses.append(loss_val)
            current_lr = lr_scheduler.get_last_lr()[0]
            logger.info("Step %d/%d loss=%.6f lr=%.2e",
                        step + 1, args.max_steps, loss_val, current_lr)

            if not vram_logged:
                vram_gb = torch.cuda.max_memory_allocated() / 1e9
                logger.info("Peak VRAM after first step: %.1f GB", vram_gb)
                vram_logged = True

            step += 1

    # Save LoRA weights
    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, "lora.safetensors")

    # Use PEFT's save method to get proper LoRA state dict
    from peft import get_peft_model_state_dict
    lora_state_dict = get_peft_model_state_dict(model)

    from safetensors.torch import save_file
    save_file(lora_state_dict, output_path)

    elapsed = time.time() - start_time
    logger.info("Training complete in %.1fs. LoRA saved to %s (%d tensors, %d bytes)",
                elapsed, output_path, len(lora_state_dict), os.path.getsize(output_path))

    # Move models off GPU to free VRAM
    model.cpu()
    # pipeline already offloaded — skip cleanup
    pipeline.text_encoder_model.cpu()
    torch.cuda.empty_cache()

    # Print final stats as JSON for the endpoint to parse
    result = {
        "status": "ok",
        "output_path": output_path,
        "elapsed_seconds": elapsed,
        "final_loss": losses[-1] if losses else None,
        "mean_loss": sum(losses) / len(losses) if losses else None,
        "steps_completed": step,
        "peak_vram_gb": torch.cuda.max_memory_allocated() / 1e9,
    }
    print("RESULT_JSON:" + json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description="ACE-Step raw LoRA trainer")
    parser.add_argument("--audio_paths", nargs="+", required=True)
    parser.add_argument("--prompts", nargs="+", required=True)
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--checkpoint_dir", default="/opt/gpu-worker/ace-step-cache")
    parser.add_argument("--max_steps", type=int, default=100)
    parser.add_argument("--learning_rate", type=float, default=1e-4)
    parser.add_argument("--lora_rank", type=int, default=16)
    parser.add_argument("--lora_target_modules", nargs="+", default=[
        "to_q", "to_k", "to_v", "to_out.0",
    ])
    args = parser.parse_args()

    train_one_run(args)


if __name__ == "__main__":
    main()
