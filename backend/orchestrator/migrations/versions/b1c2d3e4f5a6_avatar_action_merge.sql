-- Avatar action merge migration (raw SQL — alembic broken with revision cycle).
--
-- Apply with:
--   docker exec luminacast-omni-postgres-1 \
--     psql -U luminacast -d luminacast \
--     -f /path/to/b1c2d3e4f5a6_avatar_action_merge.sql
--
-- What it does:
--   1. Renames any existing `avatar_motion` / `avatar_acting` blocks to
--      the merged `avatar_action` category.
--   2. Adds `action_start_prompt` / `action_end_prompt` columns on `blocks`
--      (mirrors `body_motion_*_prompt`) for the per-block scene frame
--      prompts that seed FLUX Kontext.
--   3. Bumps alembic_version to b1c2d3e4f5a6 so future alembic runs
--      recognise this migration as applied.

BEGIN;

-- 1. Rename existing block categories to avatar_action.
UPDATE blocks
SET category = 'avatar_action'
WHERE category IN ('avatar_motion', 'avatar_acting');

-- 2. Add per-block action frame prompts (mirrors body_motion_*).
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS action_start_prompt TEXT;
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS action_end_prompt TEXT;

-- 3. Bump alembic_version. ON CONFLICT prevents duplicate-key errors when
--    re-running. We avoid an `UPDATE` because alembic_version may already
--    contain a different revision id from the prior chain; multiple rows
--    are valid if branches exist.
INSERT INTO alembic_version (version_num)
VALUES ('b1c2d3e4f5a6')
ON CONFLICT DO NOTHING;

COMMIT;
