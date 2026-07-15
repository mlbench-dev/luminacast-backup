import { useMemo } from "react";
import { Play, AudioLines, ImagePlus, Film, Image as ImageIcon, User, Sparkles, Wand2 } from "lucide-react";
import type { Block, ParallelMediaItem } from "@/lib/types";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { BlockCategoryBadge } from "./BlockCategoryBadge";
import { CrossfadePreview } from "./CrossfadePreview";

/**
 * BlockVisualPreview — the storyboard frame on the LEFT of every block
 * card. A 9:16 thumbnail that immediately tells the user what the viewer
 * will SEE while the avatar speaks. Different categories show different
 * imagery:
 *
 *   avatar_speaking  → big avatar face (full frame)
 *   avatar_voiceover → background visual + small voiceover indicator
 *   pip_talking_head         → background visual + tiny avatar PIP overlay
 *   stock_video      → stock thumbnail + play overlay
 *   stock_photo      → stock thumbnail + optional text-overlay preview
 *   generated_*      → gradient placeholder + sparkle icon
 *
 * The component is read-only on its own; the parent passes onClick to
 * open the media picker when the user wants to change the visual.
 */

interface Props {
  block: Block;
  /** Pre-resolved avatar face URL — caller pulls this off the cast/avatar. */
  avatarFaceUrl?: string | null;
  /** Estimated render duration; falls back to the script-derived estimate. */
  durationSeconds?: number | null;
  /** Click anywhere on the empty preview to open a media picker. */
  onPickMedia?: () => void;
  /** Optional override class so the parent can adjust width. */
  className?: string;
  /** Optional text overlay shown atop a stock photo (preview only). */
  textOverlay?: string | null;
  /** Pinned start-frame image URL for action blocks. When both this and
   * actionEndUrl are present, the preview crossfades between them. */
  actionStartUrl?: string | null;
  /** Pinned end-frame image URL for action blocks. */
  actionEndUrl?: string | null;
}

export function BlockVisualPreview({
  block,
  avatarFaceUrl,
  durationSeconds,
  onPickMedia,
  className,
  textOverlay,
  actionStartUrl,
  actionEndUrl,
}: Props) {
  const category = (block.category || "avatar_speaking") as string;
  // Action blocks (and any block where the user has pinned both a start
  // and end frame) get a live crossfade preview instead of the static
  // avatar face. Falls through to the existing avatar face if only one
  // frame is pinned and the category isn't action-flavoured.
  const isActionLike =
    category === "avatar_action" ||
    category === "avatar_acting" ||
    category === "avatar_motion";
  const hasBothFrames = !!actionStartUrl && !!actionEndUrl;
  const showCrossfade = hasBothFrames || (isActionLike && (!!actionStartUrl || !!actionEndUrl));

  // The block has TWO places a stock visual can live:
  //   1. parallel_media[] — b-roll overlays for avatar/PIP blocks (the
  //      canonical place; auto-populated by Smart Cast).
  //   2. stock_media_url — the headline visual for stock_video /
  //      stock_photo blocks (where the asset IS the block, not an overlay).
  //
  // We read parallel_media[0] first (it's the user's most recent pick),
  // then fall back to stock_media_url for pure stock blocks.
  const firstParallel: ParallelMediaItem | undefined = useMemo(() => {
    const m = block.parallel_media;
    return Array.isArray(m) && m.length > 0 ? m[0] : undefined;
  }, [block.parallel_media]);

  const stockThumb =
    firstParallel?.thumbnail ||
    firstParallel?.url ||
    block.stock_media_thumbnail ||
    block.stock_media_url ||
    null;
  // PR #66 Fix 1: when no explicit stock thumb is set, fall back to the
  // server-resolved default background (avatar profile face image, or an
  // LLM-suggested b-roll the Smart Cast pipeline picked). Replaces the
  // "Choose background" arrow placeholder with something the user
  // actually wants to look at while they edit the script.
  const defaultBg =
    (block as any).default_background_url ||
    avatarFaceUrl ||
    null;
  const resolvedBg = stockThumb || defaultBg;
  const stockKind: "video" | "photo" | null =
    firstParallel?.kind ?? block.stock_media_kind ?? null;
  const isAiSuggested = firstParallel?.ai_suggested === true;

  return (
    <div
      className={cn(
        // 9:16 thumbnail. 144x256 keeps the card height reasonable on a
        // 1280px laptop screen — large enough to be a real preview, not
        // so large the script zone gets squeezed.
        "relative shrink-0 w-[144px] h-[256px] rounded-xl overflow-hidden bg-black/30 border border-white/10 group/preview",
        className,
      )}
    >
      {/* ── Layer 1: the main visual ───────────────────────────────── */}
      {category === "avatar_speaking" ||
      category === "avatar_action" ||
      category === "avatar_acting" ||
      category === "avatar_motion" ? (
        showCrossfade ? (
          <CrossfadePreview
            startUrl={actionStartUrl}
            endUrl={actionEndUrl}
            alt="Action"
          />
        ) : avatarFaceUrl ? (
          <img src={avatarFaceUrl} alt="Avatar" className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <PlaceholderFace />
        )
      ) : category === "avatar_voiceover" ? (
        resolvedBg ? (
          <MediaThumb url={resolvedBg} kind={stockThumb ? stockKind : "photo"} />
        ) : (
          <EmptySlot icon={ImagePlus} label="Choose scene" onClick={onPickMedia} />
        )
      ) : category === "pip_talking_head" ? (
        <>
          {resolvedBg ? (
            <MediaThumb url={resolvedBg} kind={stockThumb ? stockKind : "photo"} />
          ) : (
            <div className="absolute inset-0 bg-gradient-to-br from-rose-500/20 via-rose-400/10 to-transparent">
              <EmptySlot icon={ImagePlus} label="Choose scene" onClick={onPickMedia} />
            </div>
          )}
          {/* Small avatar PIP — bottom-right corner, framed like a
              FaceTime tile. Sits ABOVE the duration badge so the viewer
              can spot it at a glance. */}
          {avatarFaceUrl && (
            <div className="absolute bottom-9 right-1.5 w-10 h-14 rounded-md overflow-hidden border border-white/40 shadow-lg ring-1 ring-black/40">
              <img src={avatarFaceUrl} alt="" className="w-full h-full object-cover" />
            </div>
          )}
        </>
      ) : category === "stock_video" ? (
        stockThumb ? (
          <>
            <MediaThumb url={stockThumb} kind="video" />
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="w-10 h-10 rounded-full bg-black/60 backdrop-blur-sm flex items-center justify-center ring-1 ring-white/20">
                <Play className="w-4 h-4 text-white ml-[2px]" />
              </span>
            </div>
          </>
        ) : defaultBg ? (
          // PR #66 Fix 1: show the avatar profile / LLM-suggested bg
          // until the user picks a stock video so the editor never
          // shows the arrow placeholder for an un-picked block.
          <MediaThumb url={defaultBg} kind="photo" />
        ) : (
          <EmptySlot icon={Film} label="Choose stock video" onClick={onPickMedia} />
        )
      ) : category === "stock_photo" ? (
        stockThumb ? (
          <>
            <MediaThumb url={stockThumb} kind="photo" />
            {textOverlay && (
              <div className="absolute inset-0 flex items-center justify-center p-3">
                <span
                  className="text-white font-bold text-center leading-tight"
                  style={{
                    fontSize: 13,
                    textShadow: "0 1px 4px rgba(0,0,0,.7), 0 0 24px rgba(0,0,0,.5)",
                  }}
                >
                  {textOverlay}
                </span>
              </div>
            )}
          </>
        ) : defaultBg ? (
          // PR #66 Fix 1: avatar profile / LLM-suggested fallback so we
          // never show the arrow placeholder for an un-picked block.
          <MediaThumb url={defaultBg} kind="photo" />
        ) : (
          <EmptySlot icon={ImageIcon} label="Choose stock photo" onClick={onPickMedia} />
        )
      ) : category === "generated_photo" ? (
        <GeneratedPlaceholder icon={Sparkles} label="AI photo" tone="fuchsia" />
      ) : category === "generated_video" ? (
        <GeneratedPlaceholder icon={Wand2} label="AI video" tone="violet" />
      ) : (
        <PlaceholderFace />
      )}

      {/* ── Layer 2: corner badges & overlays ──────────────────────── */}

      {/* Voiceover indicator — small waveform pill in the top-right so
          the user instantly knows the visual is BEHIND the avatar's
          voice, not the avatar themselves. */}
      {category === "avatar_voiceover" && (
        <span className="absolute top-1.5 right-1.5 inline-flex items-center gap-1 rounded-full bg-blue-600/85 ring-1 ring-blue-300/40 px-1.5 py-0.5 text-[9px] font-medium text-white backdrop-blur-sm">
          <AudioLines className="w-2.5 h-2.5" /> VO
        </span>
      )}

      {/* AI-suggested badge — sparkle dot in the top-right corner when
          the Smart Cast pipeline auto-picked this visual. Sits next to
          the VO indicator for voiceover blocks; alone for everything
          else. Subtle but noticeable. */}
      {isAiSuggested && category !== "avatar_voiceover" && (
        <span
          className="absolute top-1.5 right-1.5 inline-flex items-center gap-0.5 rounded-full bg-fuchsia-600/85 ring-1 ring-fuchsia-300/40 px-1.5 py-0.5 text-[9px] font-medium text-white backdrop-blur-sm"
          title="Picked by AI — click the b-roll picker below to swap"
        >
          <Sparkles className="w-2.5 h-2.5" /> AI
        </span>
      )}

      {/* Duration pill — tasteful top-left badge. Reads from the prop
          first (caller's authoritative estimate) then falls back to the
          backend value if present on the block. */}
      <span className="absolute top-1.5 left-1.5 inline-flex items-center rounded-md bg-black/60 backdrop-blur-sm px-1.5 py-0.5 text-[9px] font-medium text-white/85 tabular-nums">
        {formatDuration(durationSeconds ?? 0)}
      </span>

      {/* Category pill — bottom-left of the preview frame. */}
      <BlockCategoryBadge
        category={category}
        size="sm"
        className="absolute bottom-1.5 left-1.5 shadow-md"
      />

      {/* PR #76: action blocks show a voiceover state badge in the
          bottom-right so the user can scan the timeline and see at a
          glance which action blocks will speak vs. play silent. */}
      {isActionLike && (
        <span
          className="absolute bottom-1.5 right-1.5 inline-flex items-center rounded-md bg-black/65 backdrop-blur-sm px-1.5 py-0.5 text-[9px] font-medium text-white/85 shadow-md"
          title={
            (block as any).voiceover_enabled === false
              ? "Silent action — dialogue dropped at render"
              : "Dialogue plays as a voiceover over the motion clip"
          }
        >
          {(block as any).voiceover_enabled === false
            ? "🔇 silent"
            : "🎙 voiceover"}
        </span>
      )}

      {/* The visual is read-only on hover — the canonical control to add
          / change visuals is the Visual b-roll picker rendered below the
          script in the parent card. Keeping the preview non-interactive
          avoids two competing UIs that do the same thing. */}
    </div>
  );
}

/** Thumbnail rendered as a CDN-resolved image. Falls back gracefully. */
function MediaThumb({ url, kind }: { url: string; kind: "video" | "photo" | null }) {
  // url may already be absolute (Pexels CDN) — only run cdnUrl for keys
  // that look like R2 paths (no http prefix).
  const src = url.startsWith("http") ? url : cdnUrl(url);
  return (
    <img
      src={src}
      alt={kind === "video" ? "Stock video frame" : "Stock photo"}
      className="absolute inset-0 w-full h-full object-cover"
      loading="lazy"
    />
  );
}

/**
 * Empty-state placeholder when there's no media yet. We intentionally
 * render a plain div (not a button) when no `onClick` is provided so
 * the user isn't tricked into clicking a dead control — the canonical
 * way to add a visual is the Visual b-roll picker below.
 */
function EmptySlot({
  icon: Icon,
  label,
  onClick,
}: {
  icon: typeof ImagePlus;
  label: string;
  onClick?: () => void;
}) {
  const inner = (
    <>
      <Icon className="w-7 h-7 text-white/30" />
      <span className="text-[10px] font-medium leading-tight px-2 text-center text-white/45">
        {label}
      </span>
    </>
  );
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/60 hover:text-accent hover:bg-white/[0.04] transition-colors"
      >
        {inner}
      </button>
    );
  }
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-gradient-to-br from-white/[0.02] to-white/[0.05]">
      {inner}
    </div>
  );
}

/** Avatar face placeholder when no face image is available. */
function PlaceholderFace() {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-b from-indigo-500/15 to-indigo-900/20">
      <User className="w-10 h-10 text-white/30" />
    </div>
  );
}

/** AI-generated content placeholder — shimmery gradient. */
function GeneratedPlaceholder({
  icon: Icon,
  label,
  tone,
}: {
  icon: typeof Sparkles;
  label: string;
  tone: "fuchsia" | "violet";
}) {
  const gradient =
    tone === "fuchsia"
      ? "from-fuchsia-500/30 via-fuchsia-400/15 to-pink-500/20"
      : "from-violet-500/30 via-violet-400/15 to-indigo-500/20";
  return (
    <div className={cn("absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-gradient-to-br", gradient)}>
      <Icon className="w-7 h-7 text-white/70 drop-shadow" />
      <span className="text-[10px] font-medium text-white/70 tracking-wide uppercase">{label}</span>
    </div>
  );
}

function formatDuration(seconds: number) {
  if (!seconds || seconds <= 0) return "0s";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}
