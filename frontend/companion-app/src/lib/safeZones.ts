/**
 * Platform safe zones — areas where platform UI overlays content.
 *
 * Last verified: 2026-04-16
 *
 * 9:16 vertical platforms (1080×1920 reference):
 * - TikTok: top 15%, right icon column 92%×(40-85%), bottom 25% caption + nav
 * - Instagram Reels: top 10%, right icons wider, bottom 20% caption + nav
 * - YouTube Shorts: top 12%, right column minimal, bottom 22% + fatter subscribe bar
 *
 * 16:9 horizontal platforms (1920×1080 reference):
 * - YouTube: top progress/info bar 7%, right recommended sidebar, bottom controls 7%
 * - Twitter/X: top header 6%, right reply column, bottom compose 7%
 * - LinkedIn: minimal chrome — top 5%, bottom like/comment/repost bar 9%
 *
 * Safe zone overlays are visual-only — they do NOT clip the render.
 */

export type Platform =
  | "tiktok"
  | "instagram_reels"
  | "youtube_shorts"
  | "youtube"
  | "twitter"
  | "linkedin";

export type LayoutFamily = "vertical" | "horizontal";

export interface SafeZone {
  name: string;
  /** Position as percentage of canvas (0-100) */
  top: number;
  left: number;
  /** Size as percentage of canvas (0-100) */
  width: number;
  height: number;
}

export interface PlatformSafeZoneConfig {
  label: string;
  color: string;
  layout: LayoutFamily;
  /** Path to SVG overlay file */
  svgPath: string;
  zones: SafeZone[];
}

export const PLATFORM_SAFE_ZONES: Record<Platform, PlatformSafeZoneConfig> = {
  tiktok: {
    label: "TikTok",
    color: "rgba(255, 0, 100, 0.15)",
    layout: "vertical",
    svgPath: "/safe-zones/tiktok.svg",
    zones: [
      { name: "Top bar", top: 0, left: 0, width: 100, height: 15 },
      { name: "Right icons", top: 40, left: 92, width: 8, height: 45 },
      { name: "Bottom captions", top: 75, left: 0, width: 70, height: 15 },
      { name: "Bottom nav", top: 90, left: 0, width: 100, height: 10 },
    ],
  },
  instagram_reels: {
    label: "Reels",
    color: "rgba(225, 48, 108, 0.15)",
    layout: "vertical",
    svgPath: "/safe-zones/reels.svg",
    zones: [
      { name: "Top bar", top: 0, left: 0, width: 100, height: 10 },
      { name: "Right icons", top: 35, left: 91, width: 9, height: 50 },
      { name: "Bottom captions", top: 80, left: 0, width: 80, height: 10 },
      { name: "Bottom nav", top: 90, left: 0, width: 100, height: 10 },
    ],
  },
  youtube_shorts: {
    label: "Shorts",
    color: "rgba(255, 0, 0, 0.15)",
    layout: "vertical",
    svgPath: "/safe-zones/shorts.svg",
    zones: [
      { name: "Top bar", top: 0, left: 0, width: 100, height: 12 },
      { name: "Right icons", top: 50, left: 93, width: 7, height: 30 },
      { name: "Bottom description", top: 78, left: 0, width: 83, height: 12 },
      { name: "Bottom nav", top: 90, left: 0, width: 100, height: 10 },
    ],
  },
  youtube: {
    label: "YouTube",
    color: "rgba(255, 0, 0, 0.15)",
    layout: "horizontal",
    svgPath: "/safe-zones/youtube.svg",
    zones: [
      { name: "Top info bar", top: 0, left: 0, width: 100, height: 7 },
      { name: "Right sidebar", top: 7, left: 80, width: 20, height: 80 },
      { name: "Bottom controls", top: 93, left: 0, width: 100, height: 7 },
    ],
  },
  twitter: {
    label: "Twitter / X",
    color: "rgba(29, 155, 240, 0.15)",
    layout: "horizontal",
    svgPath: "/safe-zones/twitter.svg",
    zones: [
      { name: "Top header", top: 0, left: 0, width: 100, height: 6 },
      { name: "Right column", top: 6, left: 88, width: 12, height: 82 },
      { name: "Bottom compose", top: 93, left: 0, width: 100, height: 7 },
    ],
  },
  linkedin: {
    label: "LinkedIn",
    color: "rgba(10, 102, 194, 0.15)",
    layout: "horizontal",
    svgPath: "/safe-zones/linkedin.svg",
    zones: [
      { name: "Top header", top: 0, left: 0, width: 100, height: 5 },
      { name: "Bottom bar", top: 91, left: 0, width: 100, height: 9 },
    ],
  },
};

export const VERTICAL_PLATFORMS: Platform[] = ["tiktok", "instagram_reels", "youtube_shorts"];
export const HORIZONTAL_PLATFORMS: Platform[] = ["youtube", "twitter", "linkedin"];
export const ALL_PLATFORMS: Platform[] = [...VERTICAL_PLATFORMS, ...HORIZONTAL_PLATFORMS];

/**
 * Get platforms available for a given layout family.
 */
export function getPlatformsForLayout(layout: LayoutFamily): Platform[] {
  return layout === "vertical" ? VERTICAL_PLATFORMS : HORIZONTAL_PLATFORMS;
}

/**
 * Convert a percentage-based safe zone to absolute pixel coordinates.
 */
function zoneToPixels(
  zone: SafeZone,
  canvasWidth: number,
  canvasHeight: number,
): { x: number; y: number; w: number; h: number } {
  return {
    x: (zone.left / 100) * canvasWidth,
    y: (zone.top / 100) * canvasHeight,
    w: (zone.width / 100) * canvasWidth,
    h: (zone.height / 100) * canvasHeight,
  };
}

/**
 * Check if an element overlaps with any safe zone for a given platform.
 * Returns list of overlapping zone names.
 */
export function checkSafeZoneOverlap(
  elementRect: { x: number; y: number; width: number; height: number },
  canvasWidth: number,
  canvasHeight: number,
  platform: Platform,
): string[] {
  const config = PLATFORM_SAFE_ZONES[platform];
  if (!config) return [];

  const overlaps: string[] = [];

  for (const zone of config.zones) {
    const z = zoneToPixels(zone, canvasWidth, canvasHeight);

    // AABB overlap test
    const elRight = elementRect.x + elementRect.width;
    const elBottom = elementRect.y + elementRect.height;
    const zRight = z.x + z.w;
    const zBottom = z.y + z.h;

    if (
      elementRect.x < zRight &&
      elRight > z.x &&
      elementRect.y < zBottom &&
      elBottom > z.y
    ) {
      overlaps.push(`${config.label} ${zone.name}`);
    }
  }

  return overlaps;
}
