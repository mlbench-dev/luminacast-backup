-- avatar_looks: environment + mic_visible columns (raw SQL — alembic
-- revision graph is broken, see b1c2d3e4f5a6_avatar_action_merge.sql)
BEGIN;

ALTER TABLE avatar_looks
  ADD COLUMN IF NOT EXISTS environment VARCHAR(20) NOT NULL DEFAULT 'studio';

ALTER TABLE avatar_looks
  ADD COLUMN IF NOT EXISTS mic_visible BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;