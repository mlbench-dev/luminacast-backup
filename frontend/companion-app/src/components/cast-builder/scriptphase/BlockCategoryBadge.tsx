import { User, AudioLines, PictureInPicture, Film, Image as ImageIcon, Wand2, Sparkles, PersonStanding } from "lucide-react";
import { cn } from "@/lib/cn";
import type { LucideIcon } from "lucide-react";

/**
 * BlockCategoryBadge — pill badge that names the block category and gives
 * it a colour identity. Used in two places:
 *   1. Floating over the visual preview thumbnail (so the user can tell
 *      at-a-glance whether a frame is an avatar shot, a stock photo, etc.)
 *   2. Inline in the script header next to the dropdown (as a quick
 *      reminder of what was picked).
 *
 * Colour mapping mirrors the rest of the app — indigo for avatar talking
 * head, blue for voiceover, green for talking head, emerald for stock
 * video, amber for stock photo, fuchsia/violet for AI-generated.
 */

type CategoryStyle = {
  label: string;
  bg: string;
  ring: string;
  icon: LucideIcon;
};

const STYLES: Record<string, CategoryStyle> = {
  avatar_speaking: { label: "Avatar speaking", bg: "bg-indigo-600/85", ring: "ring-indigo-300/30", icon: User },
  // Merged action category (replaces avatar_motion + avatar_acting).
  avatar_action:   { label: "Action",          bg: "bg-orange-500/85", ring: "ring-orange-300/30", icon: PersonStanding },
  avatar_voiceover:{ label: "Voiceover",       bg: "bg-blue-600/85",   ring: "ring-blue-300/30",   icon: AudioLines },
  pip_talking_head:{ label: "Talking head",     bg: "bg-green-600/85",  ring: "ring-green-300/30",   icon: PictureInPicture },
  stock_video:     { label: "Stock video",     bg: "bg-emerald-600/85",ring: "ring-emerald-300/30",icon: Film },
  stock_photo:     { label: "Stock photo",     bg: "bg-amber-600/85",  ring: "ring-amber-300/30",  icon: ImageIcon },
  generated_photo: { label: "AI photo",        bg: "bg-fuchsia-600/85",ring: "ring-fuchsia-300/30",icon: Sparkles },
  generated_video: { label: "AI video",        bg: "bg-violet-600/85", ring: "ring-violet-300/30", icon: Wand2 },
};

// Legacy categories alias to avatar_action so old blocks still display correctly.
const LEGACY_ALIASES: Record<string, string> = {
  avatar_motion: "avatar_action",
  avatar_acting: "avatar_action",
  avatar_body_motion: "avatar_action",
};

export function BlockCategoryBadge({
  category,
  className,
  size = "md",
}: {
  category?: string | null;
  className?: string;
  size?: "sm" | "md";
}) {
  const key = LEGACY_ALIASES[category || ""] || category || "avatar_speaking";
  const style = STYLES[key] ?? STYLES.avatar_speaking;
  const Icon = style.icon;
  const sizeClass = size === "sm"
    ? "text-[9px] px-1.5 py-[2px] gap-1"
    : "text-[10px] px-2 py-0.5 gap-1.5";
  const iconSize = size === "sm" ? "w-2.5 h-2.5" : "w-3 h-3";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full text-white font-medium tracking-tight ring-1 backdrop-blur-sm whitespace-nowrap",
        style.bg,
        style.ring,
        sizeClass,
        className,
      )}
    >
      <Icon className={iconSize} />
      {style.label}
    </span>
  );
}

export const CATEGORY_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(STYLES).map(([k, v]) => [k, v.label]),
);
