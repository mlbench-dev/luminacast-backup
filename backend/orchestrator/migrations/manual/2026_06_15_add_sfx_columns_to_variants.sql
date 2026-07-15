-- Manual migration: add SFX marker/timing columns to variants (regression 4)
--
-- WHY MANUAL: production currently has multiple phantom alembic heads, so
-- `alembic upgrade` is unsafe. Apply this by hand against the production DB,
-- then (separately) reconcile the alembic heads. The SQLAlchemy model already
-- declares these columns as nullable JSON, so application reads keep working
-- both before and after this runs (NULL == "no SFX").
--
-- Columns:
--   sfx_markers  JSONB NULL
--       Raw markers extracted from script_text before stripping:
--       [{"name": "whoosh", "char_offset": 0, "word_index": 0}, ...]
--   sfx_timings  JSONB NULL
--       Markers resolved to absolute clip-relative start times once WhisperX
--       caption_words land: [{"name": "whoosh", "start_s": 1.23}, ...]
--
-- Idempotent: uses IF NOT EXISTS (Postgres 9.6+).

ALTER TABLE variants
    ADD COLUMN IF NOT EXISTS sfx_markers JSONB;

ALTER TABLE variants
    ADD COLUMN IF NOT EXISTS sfx_timings JSONB;
