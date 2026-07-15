# FLUX Prompt Research Notes — Avatar Pipeline

## Model: FLUX Kontext Pro (fal-ai/flux-pro/kontext)

### Identity Preservation Across Multi-Shot Generation
- **Reference image (image_url)**: The Kontext Pro model accepts a reference image and maintains the subject's identity in the output. Pass the approved front face shot as `image_url` for all angle variants.
- **Locked seed**: Using the same `seed` value across all 6 angle generations maximizes visual consistency. Combined with the Kontext reference, this achieves strong identity lock.
- **Guidance scale**: 3.5 is the sweet spot for Kontext Pro — higher values cause artifacts, lower values drift from the reference.
- **Steps**: 28 inference steps balances quality vs. speed for JPEG output.

### Prompt Scaffolding: Portrait vs Full Body
- **Portrait (face shot)**: Focus on "looking directly at camera, natural expression, portrait photo, studio lighting, sharp focus on face, 85mm lens, shallow depth of field"
- **Full body**: Include "full body visible from head to feet, vertical 9:16 composition, standing naturally" — without these, FLUX tends to crop at waist.
- **Angle modifiers**: Be explicit — "three-quarter view from the left side", not just "angled". FLUX Kontext responds well to cinematographic angle descriptions.

### Negative Prompts / Quality Control
- FLUX Kontext Pro does NOT support traditional negative prompts in the same way as SD.
- Instead, use positive exclusion phrases in the prompt: "no text, no watermarks, no overlays, clean background"
- Avoid: "ugly, bad quality, deformed" — these phrases can paradoxically introduce the artifacts they describe.
- Quality boosters that work: "photorealistic, 8K quality, sharp focus, professional photography"

### Aspect Ratio + Resolution
- Use `aspect_ratio: "9:16"` parameter (not width/height) for vertical body shots
- Default output is ~1024px on the long side
- For square portraits: omit aspect_ratio (defaults to 1:1)
- JPEG output format recommended for face/body shots (smaller file, sufficient quality)

### Key Parameters
```json
{
  "prompt": "...",
  "image_url": "reference_image_url",
  "guidance_scale": 3.5,
  "num_inference_steps": 28,
  "output_format": "jpeg",
  "seed": 12345,
  "aspect_ratio": "9:16"
}
```

### Identity Preservation Tips
1. Always use the same front-face reference across all angle shots
2. Lock the seed — same seed + same reference = maximally consistent identity
3. Repeat key identity markers in every prompt: clothing color, accessories, hair description
4. Generate front shot first, then use front shot as the reference for remaining angles
5. Body description should include "anchor points" — 2-3 unmistakable features that lock identity
