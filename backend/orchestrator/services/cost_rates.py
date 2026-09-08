"""Verified provider cost rates (May 2026).

These are the EXACT rates used to compute provider cost for every billable
action. Do not estimate — use these numbers. Update only when a provider
publishes new pricing and link the source in the PR.

User-facing prices are derived by multiplying provider cost by
`MARKUP_MULTIPLIER` (2.5x ⇒ 60% gross margin target).
"""

COST_RATES = {
    # ── LLM (OpenRouter) — per 1M tokens ──
    # Opus 4.8 is the creative-generation model (script + avatar/creative
    # descriptions) as of Step 9. Materially pricier than Sonnet: $5/$25 vs
    # $3/$15 per 1M in/out. Source: live OpenRouter account (anthropic/claude-opus-4.8).
    "openrouter/claude-opus-4.8":      {"input_per_1m": 5.00, "output_per_1m": 25.00},
    "openrouter/claude-sonnet-4":      {"input_per_1m": 3.00, "output_per_1m": 15.00},
    "openrouter/claude-sonnet-4.5":    {"input_per_1m": 3.00, "output_per_1m": 15.00},
    "openrouter/claude-sonnet-4.6":    {"input_per_1m": 3.00, "output_per_1m": 15.00},
    "openrouter/claude-3-haiku":       {"input_per_1m": 0.25, "output_per_1m": 1.25},

    # ── GPU render — per second ──
    "hostkey/any":                     0.0,        # free — our server (~€100/mo fixed)
    "runpod/a40_serverless":           0.000122,   # $0.44/hr ÷ 3600 = $0.000122/s
    "modal/l40s":                      0.000542,   # $1.95/hr ÷ 3600 (currently disabled)

    # ── fal.ai — per output unit ──
    "fal/wan_2.5_t2v":                 0.05,       # $0.05 per second of video output
    "fal/wan_2.2_t2v":                 0.10,       # $0.10 per second (older model)
    "fal/wan_2.2_i2v":                 0.10,       # $0.10 per second
    "fal/kling_2.5_turbo_pro":         0.07,       # $0.07 per second of video
    "fal/veo_3":                       0.40,       # $0.40 per second of video
    "fal/flux_kontext_pro":            0.04,       # $0.04 per image
    "fal/flux_kontext_max":            0.10,       # ~$0.10 per image
    # Nano Banana Pro (Google Gemini 3 Pro Image). fal list price, verified
    # 2026-09: $0.15/image at 1K or 2K, $0.30 at 4K. We request 2K.
    # Source: https://fal.ai/models/fal-ai/nano-banana-pro
    "fal/nano_banana_pro":             0.15,       # $0.15 per image (1K/2K)
    "fal/nano_banana_pro_4k":          0.30,       # $0.30 per 4K image
    "fal/qwen_angles":                 0.02,       # $0.02 per megapixel

    # ── Video-second backends (lip-sync / element bakes) — per output second ──
    # PR-I: these provider models price per second of OUTPUT video. Before PR-I
    # their usage rows recorded wall-clock GPU seconds at $0.00 cost because the
    # cost helper routed through calculate_gpu_render_cost (no rate table entry
    # for fal_ai/wavespeed). Public list prices as of June 2026; tune later.
    "fal/fal_hallo":                   0.05,       # Hallo lip-sync image-to-video
    "fal/sync_lipsync_v2_pro":         0.05,       # sync.so lipsync v2 pro
    "fal/sync_lipsync_v3":             0.07,       # sync.so lipsync v3
    "fal/kling_elements_v3_pro":       0.30,       # Kling v3 Pro Elements (product bake)
    "wavespeed/infinitalk":            0.04,       # WaveSpeed InfiniteTalk lip-sync

    # ── Music ──
    "mubert/startup_plan":             199.00,     # $199/mo for 5,000 tracks
    "mubert/per_track":                0.04,       # $199 ÷ 5,000 = $0.0398

    # ── Storage — Cloudflare R2 ──
    "r2/storage_gb_month":             0.015,      # $0.015/GB/month
    "r2/class_a_per_1m":               4.50,       # $4.50 per 1M write ops
    "r2/class_b_per_1m":               0.36,       # $0.36 per 1M read ops
    "r2/egress_per_gb":                0.00,       # FREE

    # ── Social publishing ──
    "zernio/per_post":                 0.016,      # ~$16/mo Build plan ÷ 1000 posts
    "zernio/comment_reply":            0.005,      # estimated

    # ── Product import ──
    "apify/single_url_scrape":         0.05,       # single product import (cheap)
    "apify/tiktok_videos":             1.00,       # avatar video analysis run

    # ── Free on HOSTKEY ──
    "hostkey/infinitetalk":            0.0,
    "hostkey/musetalk":                0.0,
    "hostkey/wanvideo_t2v":            0.0,
    "hostkey/fish_speech":             0.0,
    "hostkey/whisperx":                0.0,
    "hostkey/qwen_body_shots":         0.0,

    # ── Other free ──
    "pexels/search":                   0.0,        # free API (200 req/hr)
    "ffmpeg/compose":                  0.0,        # runs on VPS (fixed cost)

    # ── Fixed monthly costs (for admin dashboard only) ──
    "fixed/hostkey_server":            100.00,     # ~€100/mo
    "fixed/vps_hostinger":             50.00,      # ~€50/mo
    "fixed/mubert_plan":               199.00,     # $199/mo Startup
    "fixed/zernio_plan":               16.00,      # $16/mo Build (when subscribed)
}

# User pricing markup. 2.5x of provider cost ⇒ 60% gross margin.
MARKUP_MULTIPLIER = 2.5
