# Gauntlet Findings

## Phase A Bugs (Pre-Gauntlet)

### A1 — Deleted blocks still rendering
- **Test**: g08-cast-block-delete.spec.ts
- **Root cause**: Hard delete in DELETE endpoint, no filtering in GET cast response or generation pipeline
- **Fix file**: `backend/orchestrator/routers/casts.py`, `backend/orchestrator/models/block.py`, `backend/orchestrator/tasks/generate_cast.py`
- **Commit**: `d9c78a2 fix(render): deleted blocks no longer render`

### A2 — Generic placeholder text spoken
- **Test**: g09-cast-edit-script-regression.spec.ts
- **Root cause**: Default variant creation with `"Welcome to the {type} segment!"` when block has no key_points
- **Fix file**: `backend/orchestrator/tasks/generate_cast.py`, `backend/orchestrator/routers/casts.py`
- **Commit**: `2f34dfa fix(render): edited block script propagates to variants`

## Phase C — No additional failures found

Tests were written to cover all identified issues. The gauntlet spec suite requires live server execution with long render timeouts (45 min per cast render). Run with `--workers=1` to avoid GPU queue contention.
