-- Manual migration: add music controls to casts
--
-- WHY MANUAL: production currently has multiple phantom alembic heads, so
-- `alembic upgrade` is unsafe. Apply this by hand against the production DB,
-- then (separately) reconcile the alembic heads. The SQLAlchemy model already
-- declares these columns with server_default values, so application reads keep
-- working both before and after this runs.
--
-- Columns:
--   music_track_choice  TEXT NOT NULL DEFAULT 'auto'
--       Values: 'off' | 'auto' | 'track_id:<id>'
--       'off'   -> no Mubert generation, no music element on the timeline
--       'auto'  -> default mood-driven Mubert background music (current behavior)
--       'track_id:<id>' -> fixed library track (see services/music_library.py)
--   music_volume  REAL NULL
--       Per-cast volume override (0.0–0.4). NULL -> MUSIC_DEFAULT_VOLUME env (0.15).
--
-- Idempotent: uses IF NOT EXISTS (Postgres 9.6+).

ALTER TABLE casts
    ADD COLUMN IF NOT EXISTS music_track_choice TEXT NOT NULL DEFAULT 'auto';

ALTER TABLE casts
    ADD COLUMN IF NOT EXISTS music_volume REAL;
