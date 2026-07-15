-- Manual migration: canonicalize layout-template `config.face` to the four
-- layout primitives (regression-5).
--
-- WHY MANUAL: production currently has multiple phantom alembic heads, so
-- `alembic upgrade` is unsafe. Apply this by hand against the production DB,
-- then (separately) reconcile the alembic heads. The SQLAlchemy model + the
-- application code accept BOTH the old and the new `face` values (legacy
-- strings resolve through layouts.primitives.coerce_to_primitive), so reads
-- keep working before AND after this runs.
--
-- Vocabulary change (old ad-hoc → canonical primitive):
--   full        -> fullscreen
--   top_half    -> split_h           (face on the TOP half)
--   bottom_half -> split_h           (face on the BOTTOM half; none ship today)
--   pip_small   -> pip_quarter_bl
--   pip_medium  -> pip_quarter_bl
--   pip_small_bl-> pip_quarter_bl
--   pip_small_br-> pip_quarter_br
--   hidden      -> hidden            (preserved no-face state, unchanged)
--
-- Effect on the 10 shipped presets (config.face):
--   talking_head        full      -> fullscreen
--   voiceover_explainer hidden    -> hidden        (unchanged)
--   product_spotlight   top_half  -> split_h
--   split_demo          top_half  -> split_h
--   creative_motion     hidden    -> hidden        (unchanged)
--   educational_lecture pip_small -> pip_quarter_bl
--   story_vlog          full      -> fullscreen
--   ugc_review          full      -> fullscreen
--   fashion_lookbook    full      -> fullscreen
--   live_selling        full      -> fullscreen
--
-- Idempotent: rows already carrying a canonical value are left untouched by
-- the CASE (the canonical value maps to itself). Re-running is a no-op.
--
-- NOTE: layout_templates.config is a Postgres `json` column. jsonb_set needs
-- jsonb, so we round-trip through ::jsonb and cast the result back to ::json.

UPDATE layout_templates
SET config = jsonb_set(
        config::jsonb,
        '{face}',
        to_jsonb(
            CASE config ->> 'face'
                WHEN 'full'         THEN 'fullscreen'
                WHEN 'fullscreen'   THEN 'fullscreen'
                WHEN 'full_avatar'  THEN 'fullscreen'
                WHEN 'avatar_full'  THEN 'fullscreen'
                WHEN 'top_half'     THEN 'split_h'
                WHEN 'bottom_half'  THEN 'split_h'
                WHEN 'split_screen' THEN 'split_h'
                WHEN 'split_h'      THEN 'split_h'
                WHEN 'pip_small'    THEN 'pip_quarter_bl'
                WHEN 'pip_medium'   THEN 'pip_quarter_bl'
                WHEN 'pip_small_bl' THEN 'pip_quarter_bl'
                WHEN 'pip_quarter_bl' THEN 'pip_quarter_bl'
                WHEN 'pip_small_br' THEN 'pip_quarter_br'
                WHEN 'pip_quarter_br' THEN 'pip_quarter_br'
                WHEN 'hidden'       THEN 'hidden'
                -- Anything unrecognised falls back to fullscreen (mirrors
                -- coerce_to_primitive) so no row is left with stale vocabulary.
                ELSE 'fullscreen'
            END
        ),
        true
    )::json
WHERE config ? 'face';
