# Safe Zones Research — Phase F3

## TikTok (9:16 — 1080x1920)

**Source:** TikTok Creative Center — "Video Specifications for Ads & Organic Content"

Safe zones (areas occupied by platform UI):
- **Top notification bar:** 0-7% from top (0-134px) — status bar, camera/effects buttons
- **Right action buttons:** 35-75% from top, rightmost 13% (like/comment/share/save icons)
- **Bottom caption area:** 80-92% from top, left 70% (username, hashtags, caption text)
- **Bottom navigation:** 92-100% from top (home/discover/post/inbox/profile nav)
- **Shop button:** ~65-72% from top, left 35% (TikTok Shop integration)
- **Sound disc:** ~30-35% from top, right ~10% (rotating album cover)

Content safe zone (guaranteed visible): ~7-65% from top, 0-87% from left.

## Instagram Reels (9:16 — 1080x1920)

**Source:** Meta Business Help Center — "Reels Design Best Practices"

Safe zones:
- **Top bar:** 0-10% from top — profile pic, username, follow button, three-dot menu
- **Audio info:** 10-14% from top — original audio credit, trending tag
- **Right actions:** 30-70% from top, rightmost 9% (like/comment/share/save/remix)
- **Bottom caption:** 84-92% from top, left 80% (username + caption text)
- **Bottom CTA:** 92-97% from top, left 60% (shop/visit/learn more CTA button)
- **Bottom nav:** 97-100% from top (home/search/reels/shop/profile)

Content safe zone: ~14-84% from top, 0-91% from left.

## YouTube Shorts (9:16 — 1080x1920)

**Source:** YouTube Creator Academy — "Shorts Creation Best Practices"

Safe zones:
- **Status bar:** 0-9% from top — time, battery, signal, etc.
- **Right actions:** 25-70% from top, rightmost 10% (like/dislike/comment/share/remix)
- **Bottom description:** 83-91% from top, left 75% (channel name + description)
- **Subscribe button:** 91-96% from top, left 50% (subscribe/bell button)
- **Bottom nav:** 96-100% from top (home/shorts/+/subscriptions/library)

Content safe zone: ~9-83% from top, 0-90% from left.

## Implementation

All zones defined in `frontend/companion-app/src/lib/safeZones.ts` as `PLATFORM_SAFE_ZONES` map.
Each zone uses CSS percentage-based positioning for resolution independence.
The PreviewCanvas renders zones as semi-transparent red dashed overlays with labels.
The RightPropertiesPanel shows overlap warnings when an element intersects a zone.
