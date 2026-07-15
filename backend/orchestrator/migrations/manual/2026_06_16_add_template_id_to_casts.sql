-- Manual migration: add Stage-1 creative template pick to casts
--
-- WHY MANUAL: production currently has multiple phantom alembic heads, so
-- `alembic upgrade` is unsafe. Apply this by hand against the production DB,
-- then (separately) reconcile the alembic heads. The SQLAlchemy model already
-- declares this column (nullable), so application reads keep working both
-- before and after this runs.
--
-- Column:
--   template_id  VARCHAR(64) NULL
--       The Stage-1 creative template the user picked at SetupPhase
--       (see services/cast_templates.py). NULL -> "Auto / let AI choose":
--       the outline generator decides the structure freely (legacy behavior).
--       When set, the outline generator is constrained to the template's
--       block sequence + bias ratios.
--
-- Idempotent: uses IF NOT EXISTS (Postgres 9.6+).

ALTER TABLE casts
    ADD COLUMN IF NOT EXISTS template_id VARCHAR(64);
