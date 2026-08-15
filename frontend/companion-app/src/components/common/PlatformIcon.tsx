import { cn } from "@/lib/cn";

/**
 * Compact platform avatar — used in My Channels, Publish hub, and the
 * Schedule modal. Single emoji on a brand-coloured rounded square.
 *
 * Once we have proper SVG brand marks we can swap the inner span; the
 * outer container stays the same so callers don't change.
 */
export const PLATFORMS: Array<{
  id: string;
  name: string;
  description: string;
}> = [
  { id: "tiktok", name: "TikTok", description: "Reels + Shop + LIVE" },
  { id: "instagram", name: "Instagram", description: "Reels + Stories + Feed" },
  { id: "youtube", name: "YouTube", description: "Shorts + Long-form" },
  { id: "facebook", name: "Facebook", description: "Reels + Stories + Feed" },
  { id: "linkedin", name: "LinkedIn", description: "Video posts" },
  { id: "x", name: "X / Twitter", description: "Video posts" },
  { id: "pinterest", name: "Pinterest", description: "Idea Pins + Video Pins" },
];

const ICON_GLYPH: Record<string, string> = {
  tiktok: "🎵",
  instagram: "📸",
  youtube: "▶",
  facebook: "f",
  linkedin: "in",
  x: "𝕏",
  pinterest: "📌",
};

const ICON_BG: Record<string, string> = {
  tiktok: "bg-[#010101]",
  instagram: "bg-gradient-to-br from-purple-600 to-pink-500",
  youtube: "bg-red-600",
  facebook: "bg-blue-600",
  linkedin: "bg-blue-700",
  x: "bg-black",
  pinterest: "bg-red-700",
};

export function PlatformIcon({
  platform,
  className,
}: {
  platform: string;
  className?: string;
}) {
  const glyph = ICON_GLYPH[platform] ?? "?";
  const bg = ICON_BG[platform] ?? "bg-white/10";
  return (
    <div
      className={cn(
        "rounded-lg flex items-center justify-center text-white font-semibold",
        bg,
        className,
      )}
      title={platform}
    >
      <span style={{ fontSize: "0.9em", lineHeight: 1 }}>{glyph}</span>
    </div>
  );
}

export function platformLabel(id: string): string {
  return PLATFORMS.find((p) => p.id === id)?.name ?? id;
}
