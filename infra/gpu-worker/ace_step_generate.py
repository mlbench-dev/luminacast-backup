#!/usr/bin/env python3
"""ACE-Step music generation — runs in venv_ace to avoid diffusers/torch mismatch."""
import argparse, json, sys, os, wave, time

# Monkey-patch torchaudio.save/load — torchcodec not available (libnvrtc.so missing)
import torch
import torchaudio
import soundfile as sf
import numpy as np

_orig_save = torchaudio.save
def _patched_save(filepath, src, sample_rate, *args, **kwargs):
    try:
        return _orig_save(filepath, src, sample_rate, *args, **kwargs)
    except (RuntimeError, OSError, ImportError):
        data = src.cpu().numpy()
        if data.ndim == 2:
            data = data.T
        fpath = str(filepath)
        # soundfile needs a recognized extension or explicit format
        if not any(fpath.endswith(ext) for ext in ['.wav', '.flac', '.ogg', '.mp3']):
            fpath = fpath + '.wav'
        sf.write(fpath, data, sample_rate, format='WAV', subtype='FLOAT')
torchaudio.save = _patched_save

_orig_load = torchaudio.load
def _patched_load(filepath, *args, **kwargs):
    try:
        return _orig_load(filepath, *args, **kwargs)
    except (RuntimeError, OSError, ImportError):
        data, sr = sf.read(str(filepath), dtype='float32')
        tensor = torch.from_numpy(data)
        if tensor.ndim == 1:
            tensor = tensor.unsqueeze(0)
        else:
            tensor = tensor.T
        return tensor, sr
torchaudio.load = _patched_load

sys.path.insert(0, "/opt/gpu-worker/ace-step")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--lyrics", default="")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--guidance_scale", type=float, default=15.0)
    parser.add_argument("--scheduler", default="euler")
    parser.add_argument("--seed", type=int, default=-1)
    parser.add_argument("--lora_path", default="")
    parser.add_argument("--checkpoint_dir", default="/opt/gpu-worker/ace-step-cache")
    parser.add_argument("--output_dir", required=True)
    args = parser.parse_args()

    start = time.time()

    from acestep.pipeline_ace_step import ACEStepPipeline
    pipe = ACEStepPipeline(checkpoint_dir=args.checkpoint_dir)

    seeds = [args.seed] if args.seed >= 0 else None

    # Ensure models are loaded (pipeline loads lazily)
    if hasattr(pipe, 'load_checkpoint'):
        pipe.load_checkpoint()
    elif not hasattr(pipe, 'ace_step_transformer'):
        # Trigger model load by checking attributes
        pipe(audio_duration=1.0, prompt='warmup', save_path=os.path.join(args.output_dir, '_warmup'), infer_step=1)
        import glob
        for f in glob.glob(os.path.join(args.output_dir, '_warmup*')):
            os.remove(f)

    # Load LoRA via peft if specified (bypasses ACE-Step's incompatible load_lora)
    if args.lora_path and os.path.isfile(args.lora_path):
        from peft import PeftModel, LoraConfig
        from safetensors.torch import load_file
        import torch

        # Load the LoRA weights and apply to the transformer
        lora_state = load_file(args.lora_path)
        # The raw trainer saved with peft's get_peft_model, so we reload the same way
        model = pipe.ace_step_transformer
        # Create a matching LoRA config
        target_modules = set()
        for k in lora_state.keys():
            # Extract module name: base_model.model.X.Y.Z.lora_A.weight -> X.Y.Z
            parts = k.replace('base_model.model.', '').split('.')
            # Remove lora_A/lora_B.weight suffix
            module_name = '.'.join(parts[:-2])
            target_modules.add(module_name)

        # Get rank from first lora_A weight
        for k, v in lora_state.items():
            if 'lora_A' in k:
                rank = v.shape[0]
                break

        config = LoraConfig(
            r=rank,
            lora_alpha=rank,
            target_modules=list(target_modules),
            lora_dropout=0.0,
        )
        model = PeftModel.from_pretrained(
            model, args.lora_path,
            is_trainable=False,
        ) if os.path.isdir(args.lora_path) else None

        # Direct state dict load approach
        if model is None:
            from peft import get_peft_model
            model = get_peft_model(pipe.ace_step_transformer, config)
            # Load the state dict
            incompatible = model.load_state_dict(lora_state, strict=False)
            if incompatible.unexpected_keys:
                print(f'Warning: {len(incompatible.unexpected_keys)} unexpected keys')
            model.eval()
            pipe.ace_step_transformer = model
            print(f'LoRA loaded: rank={rank}, {len(target_modules)} modules')

    pipe(
        audio_duration=args.duration,
        prompt=args.prompt,
        lyrics=args.lyrics if args.lyrics else "",
        infer_step=args.steps,
        guidance_scale=args.guidance_scale,
        scheduler_type=args.scheduler,
        manual_seeds=seeds,
        lora_name_or_path='none',  # Already loaded via peft
        save_path=os.path.join(args.output_dir, 'output'),
    )

    # Find generated WAV
    wav_files = []
    for root, dirs, files in os.walk(args.output_dir):
        wav_files.extend([os.path.join(root, f) for f in files if f.endswith(".wav")])

    if not wav_files:
        print(json.dumps({"error": "No WAV file produced"}))
        sys.exit(1)

    wav_path = wav_files[0]
    try:
        with wave.open(wav_path) as wf:
            duration = wf.getnframes() / wf.getframerate()
    except:
        duration = args.duration

    elapsed = time.time() - start
    result = {
        "wav_path": wav_path,
        "duration_seconds": duration,
        "generation_time_seconds": elapsed,
    }
    print("RESULT_JSON:" + json.dumps(result))

if __name__ == "__main__":
    main()
