# Block Delete Audit — select(Block) locations

## Audited file: backend/orchestrator/routers/casts.py

| Line | Function | Query | Filter Added? | Reason |
|------|----------|-------|---------------|--------|
| 374 | `delete_cast` | `select(Block).where(Block.cast_id == cast_id)` | No | Hard-deletes all blocks (including already soft-deleted) for cascade FK cleanup |
| 407 | `retry_cast_generation` | `select(Block).where(Block.cast_id == cast_id)` | **YES** → `.where(..., Block.deleted_at.is_(None))` | Only retry non-deleted blocks' failed variants |
| 679 | `replace_all_blocks` | `select(Block).where(Block.cast_id == cast_id)` | No | Hard-deletes all existing blocks before replacing — needs all |
| 1200 | `generate_scripts` | `select(Block).where(Block.cast_id == cast_id).order_by(Block.position)` | **YES** → `.where(..., Block.deleted_at.is_(None))` | User-facing script generation — must exclude deleted |
| 1364 | `reorder_blocks` | `select(Block).where(Block.cast_id == cast_id)` | **YES** → `.where(..., Block.deleted_at.is_(None))` | User-facing reorder — must only see active blocks |
| 1426 | TTS dispatch | `select(Block).where(Block.cast_id == cast_id).options(selectinload(Block.variants))` | **YES** → `.where(..., Block.deleted_at.is_(None))` | TTS dispatch validates blocks — must exclude deleted |

## Relationship-based accesses (cast.blocks via selectinload)

| Line | Context | Already Filtered? |
|------|---------|-------------------|
| 157 | `get_cast` response builder | Yes — `if block.deleted_at is not None: continue` |
| 235 | On-the-fly overlay migration | Yes — `if blk.deleted_at is not None: continue` |
| 1489 | TTS status endpoint | Yes — `if block.deleted_at is not None: continue` |
| 1539 | Whisper transcription | Yes — `if block.deleted_at is not None: continue` |
| 1706 | Audio preview generation | Yes — filters `b.deleted_at is None` in list comprehension |

## Other files checked

- `backend/orchestrator/tasks/generate_cast.py` — No `select(Block)` calls found
- `backend/orchestrator/engine/cast_generator.py` — No `select(Block)` calls found

## Block model

`Block.deleted_at` is defined at `models/block.py:92` — nullable DateTime column with index.
No default scope or SQLAlchemy event listener exists. The soft-delete filter must be applied per-query.
