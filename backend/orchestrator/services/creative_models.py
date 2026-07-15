"""Single source of truth for the LLM model slug used by all CREATIVE
generation (script, avatar/creative descriptions, prompt enhancement).

Two env-overridable constants, both defaulting to Claude Opus 4.8:

  CAST_GENERATOR_MODEL        — script/outline generation in the cast engine
  CREATIVE_DESCRIPTION_MODEL  — avatar/creative descriptions and image
                                description (vision) creative text

They are split so a deploy can A/B one without the other, but both default
to the same slug today. Non-creative calls (content-type classifier,
consistency/"judge"/compare checks, gesture tagging, review generation,
real-time chat) deliberately do NOT read these constants — they keep their
own cheaper models.

Override per-deploy via the matching env var in the VPS .env; never hardcode
a slug at a creative call site again.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Verified OpenRouter slug (looked up against the live account in Step 9):
# context 1,000,000, prompt $5/M, completion $25/M.
_DEFAULT_CREATIVE_MODEL = "anthropic/claude-opus-4.8"

CAST_GENERATOR_MODEL = os.environ.get("CAST_GENERATOR_MODEL", _DEFAULT_CREATIVE_MODEL)
CREATIVE_DESCRIPTION_MODEL = os.environ.get(
    "CREATIVE_DESCRIPTION_MODEL", _DEFAULT_CREATIVE_MODEL
)

# Output ceilings for creative outline JSON generation. Opus is more verbose
# than the prior Sonnet default, so its outline JSON was being truncated at the
# old 2048 ceiling, breaking json.loads (and the self-correction retry hit the
# same wall). Read at call time so an env override applies without code edits.
_DEFAULT_OUTLINE_MAX_TOKENS = 8192
_DEFAULT_OUTLINE_SELF_CORRECTION_MAX_TOKENS = 8192


def get_outline_max_tokens() -> int:
    """Max output tokens for creative outline / block JSON generation.

    Override per-deploy via OUTLINE_MAX_TOKENS in the VPS .env.
    """
    return int(os.environ.get("OUTLINE_MAX_TOKENS", _DEFAULT_OUTLINE_MAX_TOKENS))


def get_outline_self_correction_max_tokens() -> int:
    """Max output tokens for the outline JSON self-correction retry.

    Override per-deploy via OUTLINE_SELF_CORRECTION_MAX_TOKENS in the VPS .env.
    """
    return int(
        os.environ.get(
            "OUTLINE_SELF_CORRECTION_MAX_TOKENS",
            _DEFAULT_OUTLINE_SELF_CORRECTION_MAX_TOKENS,
        )
    )

# Track which call sites have already logged, so we emit the audit line once
# per site per process instead of on every call.
_logged_sites: set[str] = set()


def log_creative_model_use(site: str, model: str) -> None:
    """Emit one audit log line the first time `site` uses `model`.

    Lets production logs confirm which creative call sites are on which slug
    (e.g. ``creative model=anthropic/claude-opus-4.8 site=cast_outline``).
    """
    if site in _logged_sites:
        return
    _logged_sites.add(site)
    logger.info("creative model=%s site=%s", model, site)
