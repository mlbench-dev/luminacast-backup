// Shared across cast Setup (Cast.output_format) and both avatar-creation
// flows (Avatar.layout) — same 4 values, same labels, so an avatar created
// for "9:16" and a cast created for "9:16" mean the exact same thing.
export const LAYOUT_OPTIONS = [
  { value: "9:16", label: "Vertical 9:16", desc: "1080×1920", icon: "📱" },
  { value: "16:9", label: "Horizontal 16:9", desc: "1920×1080", icon: "🖥" },
  { value: "1:1", label: "Square 1:1", desc: "1080×1080", icon: "⬜" },
  { value: "4:5", label: "4:5 Feed", desc: "1080×1350", icon: "📷" },
] as const;

export type LayoutValue = (typeof LAYOUT_OPTIONS)[number]["value"];

// Mirrors backend/orchestrator/services/aspect_conform.py's
// IMAGE_SIZE_BY_LAYOUT — used client-side to size the Clone-flow crop
// canvas to whatever layout the user picked (services/face_extraction.py's
// detect_and_frame_face remains the authoritative final framing).
export const IMAGE_SIZE_BY_LAYOUT: Record<string, { width: number; height: number }> = {
  "9:16": { width: 1024, height: 1792 },
  "16:9": { width: 1792, height: 1024 },
  "1:1": { width: 1024, height: 1024 },
  "4:5": { width: 1024, height: 1280 },
};

// A CSS `aspect-ratio` value for a video/photo player box, from an
// avatar's stored layout. Falls back to the long-standing portrait
// default for avatars with no stored layout (created before this field
// existed) — every avatar preview player used to hardcode this same 9:16
// value regardless of the avatar's real layout, which is what made a
// correctly-generated landscape video still look "vertical" in the UI.
export function playerAspectRatio(layout: string | undefined | null): string {
  if (layout === "16:9") return "16/9";
  if (layout === "1:1") return "1/1";
  if (layout === "4:5") return "4/5";
  return "9/16";
}
