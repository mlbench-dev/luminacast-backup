# A2 — Generic Placeholder Text Spoken Instead of User's Script

## Root Cause

When cast generation runs and a block has no variants, the `generate_cast.py` task creates
default variants. If the block has no `key_points`, the fallback was:

```python
script = f"Welcome to the {block.type.value} segment!"
```

This placeholder text was spoken instead of the user's actual script content.

The Block model does NOT have a `script_text` field — script text lives exclusively on
`Variant.script_text`. The user edits variant text via the ScriptPhase textarea, which calls
`PUT /api/casts/{id}/blocks/{block_id}/variants/{variant_id}` — this correctly updates the
variant. However, if audio is regenerated and the task creates new default variants (for blocks
without variants), those new variants get placeholder text.

Additionally, the rewrite endpoint had `'Hello everyone!'` as a fallback when OPENROUTER_API_KEY
was missing.

## Investigation Findings

| Finding | File | Lines |
|---------|------|-------|
| Placeholder creation (main path) | `tasks/generate_cast.py` | 136-157 |
| Placeholder creation (TTS path) | `tasks/generate_cast.py` | 447-467 |
| TTS uses variant.script_text | `engine/cast_generator.py` | 261 |
| Block model has NO script_text | `models/block.py` | 50-99 |
| Frontend edits variant directly | `ScriptPhase.tsx` | 153-194 |
| Rewrite fallback "Hello everyone!" | `routers/casts.py` | 947 |

## Fix Applied

1. **Removed placeholder text fallback** in both generation paths — if a block has no variants
   and no `key_points`, it is skipped with a warning instead of creating a variant with generic text
2. **Fixed rewrite fallback** — when OPENROUTER_API_KEY is missing, return existing variant text
   instead of prepending "Hello everyone!"
3. Blocks that have no script source (no key_points, no variants) are now logged and skipped
   rather than generating garbage audio
