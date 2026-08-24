/**
 * Cast ↔ Editor Starter mapping layer (Phase F.5).
 *
 * Two directions:
 *  1. castToEditorStarterTimeline(cast, options) → Editor Starter UndoableState
 *     Used when opening the editor for a cast — builds the initial timeline.
 *
 *  2. editorStarterToLuminacastSnapshot(undoableState) → Renderer-compatible JSON
 *     Used when auto-saving — converts Editor Starter state to the bonded V1/A1
 *     shape that extract_bonded_blocks_from_timeline() in cast_render.py expects.
 *
 * The renderer contract (sacred):
 *   { tracks: [{ type, elements: [{ id, type, s, e, props: {src}, metadata: {block_id, bonded, paired_*} }] }], version: 1 }
 *
 * The `twick_data` field name is kept for backward compat. Its content is now
 * the OUTPUT of editorStarterToLuminacastSnapshot() — the renderer-format payload,
 * NOT Editor Starter's native state. The backend never sees Editor Starter shapes.
 */
import type { Cast, Block, Variant } from "@/lib/types";
import type { UndoableState, TrackType } from "@/components/cast-builder/editor-starter/state/types";
import type { EditorStarterItem } from "@/components/cast-builder/editor-starter/items/item-type";
import type { ImageItem } from "@/components/cast-builder/editor-starter/items/image/image-item-type";
import type { AudioItem } from "@/components/cast-builder/editor-starter/items/audio/audio-item-type";
import type { VideoItem } from "@/components/cast-builder/editor-starter/items/video/video-item-type";
// NOTE: Captions used to be emitted as `TextItem` chunks (5-word strips) on a
// dedicated caption track — that was "Path A". Path B (the current approach)
// emits ONE `CaptionsItem` per block backed by a `CaptionAsset`, and the
// CaptionsLayer renders sentence-level pages via @remotion/captions'
// `createTikTokStyleCaptions`. The TextItem import is intentionally not
// re-added here — captions never become text items anymore.
import type { EditorStarterAsset, ImageAsset, AudioAsset, VideoAsset, CaptionAsset } from "@/components/cast-builder/editor-starter/assets/assets";
import type { ItemMetadata } from "@/components/cast-builder/editor-starter/items/shared";
import type { CaptionsItem } from "@/components/cast-builder/editor-starter/items/captions/captions-item-type";
import type { Caption } from "@remotion/captions";
import { CAPTION_PRESETS, presetPositionFraction } from "./captionPresets";
import { cdnUrl } from "./cdn";
import { getCategoryInfo } from "@/lib/blockCategories";

// Re-export BlockRegion for backward compat with existing callers
export interface BlockRegion {
  block_id: string;
  block_position: number;
  variant_id: string;
  start_s: number;
  end_s: number;
}

// Mirrors services/sfx_library.py's SFX_CATALOG exactly (18 names — the
// marker grammar the LLM emits — keep both lists in lockstep). Kept as a
// small static table here rather than fetched from the backend because
// castToEditorStarterTimeline is a synchronous, pure mapping function; this
// catalog is fixed and rarely changes.
const SFX_CATALOG: Record<string, { durationS: number; volume: number }> = {
  whoosh: { durationS: 0.5, volume: 1.0 },
  pop: { durationS: 0.3, volume: 1.0 },
  ding: { durationS: 0.5, volume: 1.0 },
  cash_register: { durationS: 0.8, volume: 1.0 },
  sparkle: { durationS: 0.7, volume: 1.0 },
  record_scratch: { durationS: 0.6, volume: 1.0 },
  swoosh_up: { durationS: 0.5, volume: 1.0 },
  swoosh_down: { durationS: 0.5, volume: 1.0 },
  notification: { durationS: 0.4, volume: 1.0 },
  timer_tick: { durationS: 0.3, volume: 1.0 },
  click: { durationS: 0.2, volume: 1.0 },
  drumroll: { durationS: 1.2, volume: 1.0 },
  applause: { durationS: 1.5, volume: 1.0 },
  camera_shutter: { durationS: 0.3, volume: 1.0 },
  bass_drop: { durationS: 0.5, volume: 1.0 },
  typing: { durationS: 0.8, volume: 1.0 },
  coin: { durationS: 0.4, volume: 1.0 },
  success: { durationS: 0.5, volume: 1.0 },
};

function sfxUrl(name: string): string {
  return `https://media.luminacast.com/sfx/${name}.wav`;
}

/**
 * Strip internal script direction markers — `[sfx:*]`/`[pause]` and
 * `(excited)`/`(whispering)` prosody — so they never leak into rendered
 * captions. Mirrors backend `utils/script_cleaning.strip_script_markers`.
 */
export function stripScriptMarkers(text: string): string {
  if (!text) return "";
  return text
    .replace(/\[[^\]]*\]/g, " ")
    .replace(/\([^)]*\)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Canvas dimensions by output format */
export function getCanvasSize(outputFormat?: string): { width: number; height: number } {
  switch (outputFormat) {
    case "16:9": return { width: 1920, height: 1080 };
    case "1:1":  return { width: 1080, height: 1080 };
    case "4:5":  return { width: 1080, height: 1350 };
    default:     return { width: 1080, height: 1920 }; // 9:16
  }
}

const DEFAULT_FPS = 30;

/** Generate a random ID for Editor Starter items/assets/tracks */
function randomId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Seconds → frames at given fps */
function secondsToFrames(seconds: number, fps: number): number {
  return Math.round(seconds * fps);
}

/**
 * Seconds → frames, rounded UP, for clip durations that must fully cover an
 * audio track (e.g. the bonded voiceover pair below). secondsToFrames's
 * round-to-nearest quantizes to the frame grid, which rounds down roughly
 * half the time — producing a clip a fraction of a frame shorter than the
 * exact (unquantized) tts_duration_seconds it's meant to hold. The Phase 3
 * render validator compares the frame-quantized slot against that exact
 * value, so any downward rounding fails it. Rounding up guarantees the slot
 * always covers the full audio.
 */
function secondsToFramesCeil(seconds: number, fps: number): number {
  return Math.ceil(seconds * fps);
}

/** Frames → seconds at given fps */
function framesToSeconds(frames: number, fps: number): number {
  return frames / fps;
}

// ─── F.5.1 castToEditorStarterTimeline ──────────────────────────

export interface CastToEditorOptions {
  avatarFaceKey?: string;
  avatarName?: string;
  activeVariantPerBlock?: Record<string, string>;
  fps?: number;
  captionsEnabled?: boolean;
}

/**
 * Build Editor Starter UndoableState from a Cast.
 *
 * For each block, creates:
 *  - An image asset (avatar face) or video asset (if rendered video exists)
 *  - An audio asset (TTS voice)
 *  - An ImageItem/VideoItem on the video track with metadata.block_id
 *  - An AudioItem on the audio track with metadata.block_id
 *  - Items are bonded via metadata.paired_*_element_id
 */
export function castToEditorStarterTimeline(
  cast: Cast,
  options: CastToEditorOptions = {},
): { state: UndoableState; blockRegions: BlockRegion[] } {
  const fps = options.fps ?? DEFAULT_FPS;
  const canvas = getCanvasSize(cast.output_format);
  const avatarLabel = options.avatarName || "Avatar";

  const blocks = (cast.blocks || [])
    .filter((b: any) => b.is_active !== false)
    .sort((a: any, b2: any) => (a.position ?? 0) - (b2.position ?? 0));

  const regions: BlockRegion[] = [];
  const items: Record<string, EditorStarterItem> = {};
  const assets: Record<string, EditorStarterAsset> = {};

  const videoTrackId = "track-video";
  const audioTrackId = "track-audio";
  const productTrackId = "track-products";
  const musicTrackId = "track-music";
  const sfxTrackId = "track-sfx";

  const videoTrackItemIds: string[] = [];
  const audioTrackItemIds: string[] = [];
  const musicTrackItemIds: string[] = [];
  const sfxTrackItemIds: string[] = [];
  const captionTrackItemIds: string[] = [];
  const captionTrackId = "caption_track";
  const productTrackItemIds: string[] = [];

  // Build product lookup from cast.products. The serialized shape is
  // {id, name, cover_image_url, cover_image_key}. PR #20 extends it with
  // an `assets` array; we read it defensively so this function still works
  // before that PR lands (carousel just falls back to the single cover).
  const productMap = new Map<string, {
    id: string;
    name: string;
    cover_image_url?: string;
    cover_image_key?: string;
    assets?: Array<{ id: string; r2_url: string; media_type: string }>;
  }>();
  for (const p of (cast as any).products || []) {
    if (p.id) productMap.set(p.id, p);
  }
  // Ordered list of cast-level products for round-robin fallback when an
  // individual block doesn't have its own product_id (matches the backend
  // pattern in routers/casts.py where new blocks cycle through
  // selected_product_ids by position).
  const castProducts: Array<{ id: string; name: string; cover_image_url?: string; cover_image_key?: string }> =
    ((cast as any).products || []).filter((p: { id?: string }) => !!p.id);
  let blockProductCursor = 0;

  let cursor = 0; // seconds

  for (const block of blocks) {
    const variantId =
      options.activeVariantPerBlock?.[block.id] || block.variants?.[0]?.id || "";
    const variant = block.variants?.find((v: Variant) => v.id === variantId);
    if (!variant) {
      // Block without audio (e.g., newly added) — create a 3-second placeholder
      const phDur = 3; // seconds
      const phStart = cursor;
      const phEnd = cursor + phDur;
      const phBlockNum = (block.position ?? 0) + 1;
      const phItemId = `ph_${block.id}`;
      const phAssetId = `asset_ph_${block.id}`;
      const phFaceSrc = options.avatarFaceKey ? cdnUrl(options.avatarFaceKey) : "";
      const phIsPip = block.category === "pip" || block.category === "pip_talking_head";
      const phIsVoiceover =
        block.render_mode === "voiceover" ||
        block.category === "avatar_voiceover" ||
        block.category?.startsWith("voiceover_");
      const phIsBodyMotion =
        (block as any).render_mode === "body_motion" ||
        block.category === "avatar_action" ||
        block.category === "avatar_acting" ||
        block.category === "avatar_body_motion";
      const phIsMotion =
        block.category === "avatar_motion" ||
        (block as any).render_mode === "motion";

      const phAsset: ImageAsset = {
        type: "image",
        id: phAssetId,
        filename: `${avatarLabel} \u2014 Block ${phBlockNum} (no audio)`,
        size: 0,
        remoteUrl: phFaceSrc,
        remoteFileKey: null,
        mimeType: "image/png",
        width: canvas.width,
        height: canvas.height,
      };
      assets[phAssetId] = phAsset;

      const phItem: ImageItem = {
        type: "image",
        id: phItemId,
        assetId: phAssetId,
        from: secondsToFrames(phStart, fps),
        durationInFrames: secondsToFrames(phDur, fps),
        top: 0,
        left: 0,
        width: canvas.width,
        height: canvas.height,
        opacity: 1,
        isDraggingInTimeline: false,
        keepAspectRatio: true,
        fadeInDurationInSeconds: 0,
        fadeOutDurationInSeconds: 0,
        borderRadius: 0,
        rotation: 0,
        cropLeft: 0,
        cropTop: 0,
        cropRight: 0,
        cropBottom: 0,
        metadata: {
          block_id: block.id,
          bonded: true,
          placeholder: true,
          track_type: "video_face",
          category_color: getCategoryInfo(block.category).hex,
          render_mode: phIsPip
            ? "pip"
            : phIsVoiceover
              ? "voiceover"
              : phIsBodyMotion
                ? "body_motion"
                : phIsMotion
                  ? "motion"
                  : "full",
        },
      };
      items[phItemId] = phItem;
      videoTrackItemIds.push(phItemId);

      cursor = phEnd;
      continue;
    }

    const dur = variant.tts_duration_seconds || variant.duration_seconds || 5;
    // Rounded up (not secondsToFrames' round-to-nearest) so the bonded
    // audio/video/image items below never come out a fraction of a frame
    // shorter than the actual voiceover — see secondsToFramesCeil.
    const durFrames = secondsToFramesCeil(dur, fps);
    const start = cursor;
    // Derived from durFrames (not raw `dur`) so the next block's cursor
    // lands exactly on the frame boundary this block's items actually end
    // on — otherwise ceil-rounding durFrames up could let this block's
    // audio/video overlap the next block's by a fraction of a frame.
    const end = start + durFrames / fps;

    regions.push({
      block_id: block.id,
      block_position: block.position ?? 0,
      variant_id: variant.id,
      start_s: start,
      end_s: end,
    });

    const snapshotItemId = `v1_${block.id}`;
    const voiceItemId = `a1_${block.id}`;
    // FIX 5 — shared id on both halves of the V1+A1 bonded pair so the
    // timeline can find the sibling at runtime via metadata.bonded_pair_id.
    const bondedPairId = `bond_${block.id}`;

    // Determine V1 source: prefer rendered video, fall back to avatar face image
    const videoSrc =
      (variant.final_video_key ? cdnUrl(variant.final_video_key) : "") ||
      variant.stream_url ||
      variant.clip_url ||
      (variant.video_key ? cdnUrl(variant.video_key) : "");
    const faceSrc = options.avatarFaceKey ? cdnUrl(options.avatarFaceKey) : "";
    const v1Src = videoSrc || faceSrc;
    const useVideo = !!videoSrc;

    // Audio source
    const audioSrc =
      variant.audio_url ||
      (variant.audio_key ? cdnUrl(variant.audio_key) : "");

    const blockNum = (block.position ?? 0) + 1;
    const isPip = block.category === "pip" || block.category === "pip_talking_head";
    // The block's own `render_mode` column (set explicitly by the backend,
    // e.g. for stock_photo/stock_video blocks used as voiceover B-roll) is
    // the source of truth — category string-matching alone missed blocks
    // like category="stock_photo" + render_mode="voiceover", which fell
    // through to the "full" default below and got dispatched through the
    // full avatar-motion generation pipeline (Kling/WaveSpeed) instead of
    // the voiceover placeholder+overlay path. That fed a static photo into
    // a talking-head model, producing a consistently frozen clip that only
    // surfaced as a confusing Phase 3 "clip_mostly_frozen" rejection.
    const isVoiceover =
      block.render_mode === "voiceover" ||
      block.category === "avatar_voiceover" ||
      block.category?.startsWith("voiceover_");
    // T2V (no avatar) — only generated_video uses this path now; legacy
    // avatar_motion blocks fall through to body_motion via category alias.
    const isMotion =
      block.category === "avatar_motion" || (block as any).render_mode === "motion";
    // avatar_action / body_motion: avatar interpolating between two AI-
    // generated SCENE frames (FLUX Kontext) — replaces avatar_acting body
    // shots. Distinct orange badge.
    const isBodyMotion =
      (block as any).render_mode === "body_motion" ||
      block.category === "avatar_action" ||
      block.category === "avatar_acting" ||
      block.category === "avatar_body_motion";
    const motionPrompt =
      (block as any).motion_prompt ||
      (block as any).body_motion_prompt ||
      variant.motion_prompt ||
      "";
    // Carry the block category color through to the timeline so each item
    // strip in the editor matches the pill the user picked in the script
    // editor — consistent visual language across the whole UI.
    const categoryHex = getCategoryInfo(block.category).hex;

    // PR #83 — Talking-head PIP window.
    //
    // Sources of truth, in priority order:
    //   1. block.metadata.pip_layout — explicit user / LLM choice.
    //      Two vocabularies have existed here and BOTH must be handled:
    //        - PR #83's own: fullscreen / pip_small / pip_medium / hidden.
    //        - services.layouts.primitives.LayoutPrimitive (engine/
    //          cast_generator.py's product/b-roll injection uses this):
    //          fullscreen / split_h / pip_quarter_bl / pip_quarter_br.
    //      Before this fix, pip_quarter_bl/br fell through unrecognized to
    //      the legacy category check below — which only ever renders
    //      bottom-RIGHT, so a "bl" block silently rendered on the wrong
    //      side, and any block without category="pip_talking_head" (e.g.
    //      injected product/b-roll beats that don't set that category)
    //      fell all the way through to fullscreen — visually indistinguishable
    //      from the old split_h days despite the DB no longer saying split_h.
    //   2. Legacy category-based PIP (block.category === "pip" |
    //      "pip_talking_head") — kept for any cast that pre-dates this
    //      field. Renders as the legacy bottom-right 30% window.
    //   3. Default fullscreen.
    //
    // Sizes match services/timeline_builder.py — the renderer reads the
    // SAME shape so preview ≈ output.
    const pipLayoutMeta = (block as any).metadata?.pip_layout as
      | "fullscreen"
      | "pip_small"
      | "pip_medium"
      | "hidden"
      | "pip_quarter_bl"
      | "pip_quarter_br"
      | "split_h"
      | undefined;
    const isPipSmall = pipLayoutMeta === "pip_small";
    const isPipMedium = pipLayoutMeta === "pip_medium";
    const isPipHidden = pipLayoutMeta === "hidden";
    const isPipQuarterBl = pipLayoutMeta === "pip_quarter_bl";
    const isPipQuarterBr = pipLayoutMeta === "pip_quarter_br";
    const isPipQuarter = isPipQuarterBl || isPipQuarterBr;
    const isPipFromMeta = isPipSmall || isPipMedium || isPipQuarter;

    // Side length at the reference 1920 px canvas height. Scale to the
    // actual canvas height so a future non-reference output keeps the
    // same visual fraction.
    const refH = 1920;
    let pipW: number;
    let pipH: number;
    let pipLeft: number;
    let pipTop: number;
    let pipBorderRadius: number;
    if (isPipSmall) {
      const side = Math.round(346 * canvas.height / refH);
      pipW = side;
      pipH = side;
      pipLeft = Math.round(24 * canvas.height / refH);
      pipTop = Math.round(24 * canvas.height / refH);
      pipBorderRadius = 24;
    } else if (isPipMedium) {
      const side = Math.round(480 * canvas.height / refH);
      pipW = side;
      pipH = side;
      pipLeft = Math.round(24 * canvas.height / refH);
      pipTop = Math.round(24 * canvas.height / refH);
      pipBorderRadius = 24;
    } else if (isPipQuarter) {
      // Mirrors layouts.primitives.compute_geometry's pip_quarter_bl/br:
      // a ~quarter-canvas-area face anchored to a bottom corner with a
      // ~4%-of-shorter-side safe-area margin.
      const margin = Math.round(0.04 * Math.min(canvas.width, canvas.height));
      pipW = Math.round(canvas.width / 2 - margin * 1.5);
      pipH = Math.round(canvas.height / 2 - margin);
      pipTop = canvas.height - pipH - margin;
      pipLeft = isPipQuarterBr
        ? canvas.width - pipW - margin
        : margin;
      pipBorderRadius = 16;
    } else if (isPip) {
      // Legacy category-based PIP (bottom-right 30%, kept for casts
      // that haven't been migrated to pip_layout yet).
      pipW = Math.round(canvas.width * 0.3);
      pipH = Math.round(canvas.height * 0.3);
      pipLeft = canvas.width - pipW - 40;
      pipTop = canvas.height - pipH - 200;
      pipBorderRadius = 16;
    } else {
      pipW = canvas.width;
      pipH = canvas.height;
      pipLeft = 0;
      pipTop = 0;
      pipBorderRadius = 0;
    }
    const isAnyPip = isPip || isPipFromMeta;

    // Voiceover blocks: synthesise an INVISIBLE placeholder V1 so the
    // V1+A1 bonded-pair invariant holds. The renderer's bonded-block
    // extractor required both halves to recognise a block; without a V1
    // the voiceover block was silently dropped from the render and the
    // user lost the narration entirely. The placeholder is opacity 0,
    // sourced from the avatar's idle face image (or first parallel-media
    // item), and exists purely so the extractor can pair it with A1. The
    // actual visual the viewer sees is the parallel-media overlay items.
    const voiceoverPlaceholderSrc: string = isVoiceover ? (
      v1Src ||
      ((block as any).parallel_media?.[0]?.thumbnail) ||
      ((block as any).parallel_media?.[0]?.url) ||
      ""
    ) : "";

    // Create V1 asset + item (skip for voiceover blocks — no face).
    // PR #83: pip_layout=hidden also skips V1 — the audio still rides on
    // A1 but no face is rendered.
    if (v1Src && !isVoiceover && !isPipHidden) {
      const v1AssetId = `asset_v1_${block.id}`;

      if (useVideo) {
        const videoAsset: VideoAsset = {
          type: "video",
          id: v1AssetId,
          filename: `${avatarLabel} \u2014 Block ${blockNum}`,
          size: 0,
          remoteUrl: v1Src,
          remoteFileKey: null,
          mimeType: "video/mp4",
          durationInSeconds: dur,
          hasAudioTrack: false,
          width: canvas.width,
          height: canvas.height,
        };
        assets[v1AssetId] = videoAsset;

        const videoItem: VideoItem = {
          type: "video",
          id: snapshotItemId,
          assetId: v1AssetId,
          from: secondsToFrames(start, fps),
          durationInFrames: durFrames,
          top: pipTop,
          left: pipLeft,
          width: pipW,
          height: pipH,
          opacity: 1,
          isDraggingInTimeline: false,
          videoStartFromInSeconds: 0,
          decibelAdjustment: 0,
          playbackRate: 1,
          audioFadeInDurationInSeconds: 0,
          audioFadeOutDurationInSeconds: 0,
          fadeInDurationInSeconds: 0,
          fadeOutDurationInSeconds: 0,
          keepAspectRatio: true,
          borderRadius: pipBorderRadius,
          rotation: 0,
          cropLeft: 0,
          cropTop: 0,
          cropRight: 0,
          cropBottom: 0,
          metadata: {
            block_id: block.id,
            bonded: true,
            bonded_pair_id: bondedPairId,
            paired_audio_element_id: voiceItemId,
            duration_mode: "crop" as const,
            track_type: "video_face",
            motion_prompt: motionPrompt || variant.motion_prompt || "",
            render_mode: isAnyPip
              ? "pip"
              : isVoiceover
                ? "voiceover"
                : isBodyMotion
                  ? "body_motion"
                  : isMotion
                    ? "motion"
                    : "full",
            category_color: categoryHex,
            ...(isPip ? { pip_position: "bottom_right", pip_scale: 0.3 } : {}),
            // PR #83 — PIP layout + edge treatment. The renderer reads
            // pip_layout to switch bake size + the compose stage reads
            // feather/drop_shadow to apply the rounded-rect alpha mask.
            ...(pipLayoutMeta ? { pip_layout: pipLayoutMeta } : {}),
            ...(isPipFromMeta
              ? { feather: 12, drop_shadow: { blur: 10, alpha: 0.5 } }
              : {}),
          },
        };
        items[snapshotItemId] = videoItem;
      } else {
        const imageAsset: ImageAsset = {
          type: "image",
          id: v1AssetId,
          filename: `${avatarLabel} \u2014 Block ${blockNum}`,
          size: 0,
          remoteUrl: v1Src,
          remoteFileKey: null,
          mimeType: "image/png",
          width: canvas.width,
          height: canvas.height,
        };
        assets[v1AssetId] = imageAsset;

        const imageItem: ImageItem = {
          type: "image",
          id: snapshotItemId,
          assetId: v1AssetId,
          from: secondsToFrames(start, fps),
          durationInFrames: durFrames,
          top: pipTop,
          left: pipLeft,
          width: pipW,
          height: pipH,
          opacity: 1,
          isDraggingInTimeline: false,
          keepAspectRatio: true,
          fadeInDurationInSeconds: 0,
          fadeOutDurationInSeconds: 0,
          borderRadius: pipBorderRadius,
          rotation: 0,
          cropLeft: 0,
          cropTop: 0,
          cropRight: 0,
          cropBottom: 0,
          metadata: {
            block_id: block.id,
            bonded: true,
            bonded_pair_id: bondedPairId,
            paired_audio_element_id: voiceItemId,
            duration_mode: "crop" as const,
            track_type: "video_face",
            motion_prompt: motionPrompt || variant.motion_prompt || "",
            render_mode: isAnyPip
              ? "pip"
              : isVoiceover
                ? "voiceover"
                : isBodyMotion
                  ? "body_motion"
                  : isMotion
                    ? "motion"
                    : "full",
            category_color: categoryHex,
            ...(isPip ? { pip_position: "bottom_right", pip_scale: 0.3 } : {}),
            // PR #83 — PIP layout + edge treatment. The renderer reads
            // pip_layout to switch bake size + the compose stage reads
            // feather/drop_shadow to apply the rounded-rect alpha mask.
            ...(pipLayoutMeta ? { pip_layout: pipLayoutMeta } : {}),
            ...(isPipFromMeta
              ? { feather: 12, drop_shadow: { blur: 10, alpha: 0.5 } }
              : {}),
            // This block's real avatar video hasn't been generated yet (that
            // only happens at Finalize & Render) — we're standing in with a
            // static face photo so captions/overlays/timing can still be
            // arranged against something. ImageLayer reads this to badge the
            // frame so it isn't mistaken for the actual final footage.
            is_motion_placeholder: true,
          },
        };
        items[snapshotItemId] = imageItem;
      }
      videoTrackItemIds.push(snapshotItemId);
    } else if (isVoiceover && voiceoverPlaceholderSrc) {
      // Voiceover placeholder V1 — invisible (opacity 0), full-canvas,
      // pinned to the same time range as A1 so the bonded pair lines up.
      // The actual visual the viewer sees is the parallel-media overlays
      // built later in this function.
      const v1AssetId = `asset_v1_${block.id}`;
      const placeholderAsset: ImageAsset = {
        type: "image",
        id: v1AssetId,
        filename: `${avatarLabel} \u2014 Voiceover ${blockNum} (placeholder)`,
        size: 0,
        remoteUrl: voiceoverPlaceholderSrc,
        remoteFileKey: null,
        mimeType: "image/png",
        width: canvas.width,
        height: canvas.height,
      };
      assets[v1AssetId] = placeholderAsset;
      const placeholderItem: ImageItem = {
        type: "image",
        id: snapshotItemId,
        assetId: v1AssetId,
        from: secondsToFrames(start, fps),
        durationInFrames: durFrames,
        top: 0,
        left: 0,
        width: canvas.width,
        height: canvas.height,
        opacity: 0,  // invisible — actual visual is the parallel_media overlay
        isDraggingInTimeline: false,
        keepAspectRatio: true,
        fadeInDurationInSeconds: 0,
        fadeOutDurationInSeconds: 0,
        borderRadius: 0,
        rotation: 0,
        cropLeft: 0,
        cropTop: 0,
        cropRight: 0,
        cropBottom: 0,
        metadata: {
          block_id: block.id,
          bonded: true,
          bonded_pair_id: bondedPairId,
          paired_audio_element_id: voiceItemId,
          duration_mode: "crop" as const,
          track_type: "video_face",
          render_mode: "voiceover",
          category_color: categoryHex,
          placeholder: true,
        },
      };
      items[snapshotItemId] = placeholderItem;
      videoTrackItemIds.push(snapshotItemId);
    }

    // Create A1 asset + item
    if (audioSrc) {
      const a1AssetId = `asset_a1_${block.id}`;
      const audioAsset: AudioAsset = {
        type: "audio",
        id: a1AssetId,
        filename: `Voice \u2014 Block ${blockNum}`,
        size: 0,
        remoteUrl: audioSrc,
        remoteFileKey: null,
        mimeType: "audio/wav",
        durationInSeconds: dur,
      };
      assets[a1AssetId] = audioAsset;

      const audioItem: AudioItem = {
        type: "audio",
        id: voiceItemId,
        assetId: a1AssetId,
        from: secondsToFrames(start, fps),
        durationInFrames: durFrames,
        top: 0,
        left: 0,
        width: 0,
        height: 0,
        opacity: 1,
        isDraggingInTimeline: false,
        audioStartFromInSeconds: 0,
        decibelAdjustment: 0,
        playbackRate: 1,
        audioFadeInDurationInSeconds: 0,
        audioFadeOutDurationInSeconds: 0,
        metadata: {
          block_id: block.id,
          bonded: true,
          bonded_pair_id: bondedPairId,
          paired_video_element_id: snapshotItemId,
          track_type: "audio_voice",
          category_color: categoryHex,
        },
      };
      items[voiceItemId] = audioItem;
      audioTrackItemIds.push(voiceItemId);
    }

    // ── Parallel b-roll ───────────────────────────────────────────
    // Stock photos / videos that play ON TOP of the avatar's voice for
    // this block. Items are spread sequentially across the block's
    // duration; if explicit `start_offset_s` / `duration_s` are present
    // we honour them. Each item is added to videoTrackItemIds so it
    // sits ABOVE the V1 avatar / voice strip in the editor.
    const parallelMedia = (block as any).parallel_media as
      | Array<{
          kind: "video" | "photo";
          url: string;
          thumbnail?: string;
          pexels_id?: string | null;
          source?: string;
          start_offset_s?: number;
          duration_s?: number | null;
        }>
      | undefined;
    if (Array.isArray(parallelMedia) && parallelMedia.length > 0) {
      // Compute default per-item slot when offsets are missing.
      const slot = dur / parallelMedia.length;
      parallelMedia.forEach((pm, pmIdx) => {
        if (!pm || !pm.url || !pm.kind) return;
        const offset = typeof pm.start_offset_s === "number"
          ? Math.max(0, Math.min(pm.start_offset_s, dur - 0.1))
          : pmIdx * slot;
        const itemDur = typeof pm.duration_s === "number" && pm.duration_s !== null
          ? Math.max(0.1, Math.min(pm.duration_s, dur - offset))
          : slot;
        const itemFrom = secondsToFrames(start + offset, fps);
        const itemFrames = Math.max(
          1,
          secondsToFrames(itemDur, fps),
        );

        const pmItemId = `pm_${block.id}_${pmIdx}`;
        const pmAssetId = `asset_pm_${block.id}_${pmIdx}`;
        if (pm.kind === "video") {
          const pmAsset: VideoAsset = {
            type: "video",
            id: pmAssetId,
            filename: `B-roll ${pmIdx + 1} \u2014 Block ${blockNum}`,
            size: 0,
            remoteUrl: pm.url,
            remoteFileKey: null,
            mimeType: "video/mp4",
            durationInSeconds: itemDur,
            hasAudioTrack: false,
            width: canvas.width,
            height: canvas.height,
          };
          assets[pmAssetId] = pmAsset;
          const pmItem: VideoItem = {
            type: "video",
            id: pmItemId,
            assetId: pmAssetId,
            from: itemFrom,
            durationInFrames: itemFrames,
            top: 0,
            left: 0,
            width: canvas.width,
            height: canvas.height,
            opacity: 1,
            isDraggingInTimeline: false,
            keepAspectRatio: true,
            fadeInDurationInSeconds: 0,
            fadeOutDurationInSeconds: 0,
            borderRadius: 0,
            rotation: 0,
            cropLeft: 0,
            cropTop: 0,
            cropRight: 0,
            cropBottom: 0,
            videoStartFromInSeconds: 0,
            playbackRate: 1,
            decibelAdjustment: -60, // mute the b-roll's own audio
            audioFadeInDurationInSeconds: 0,
            audioFadeOutDurationInSeconds: 0,
            metadata: {
              block_id: block.id,
              track_type: "parallel_media",
              category_color: categoryHex,
              parallel_kind: "video",
              parallel_source: pm.source || "pexels",
            },
          };
          items[pmItemId] = pmItem;
          videoTrackItemIds.push(pmItemId);
        } else {
          // photo
          const pmAsset: ImageAsset = {
            type: "image",
            id: pmAssetId,
            filename: `B-roll ${pmIdx + 1} \u2014 Block ${blockNum}`,
            size: 0,
            remoteUrl: pm.url,
            remoteFileKey: null,
            mimeType: "image/jpeg",
            width: canvas.width,
            height: canvas.height,
          };
          assets[pmAssetId] = pmAsset;
          const pmItem: ImageItem = {
            type: "image",
            id: pmItemId,
            assetId: pmAssetId,
            from: itemFrom,
            durationInFrames: itemFrames,
            top: 0,
            left: 0,
            width: canvas.width,
            height: canvas.height,
            opacity: 1,
            isDraggingInTimeline: false,
            keepAspectRatio: true,
            fadeInDurationInSeconds: 0,
            fadeOutDurationInSeconds: 0,
            borderRadius: 0,
            rotation: 0,
            cropLeft: 0,
            cropTop: 0,
            cropRight: 0,
            cropBottom: 0,
            metadata: {
              block_id: block.id,
              track_type: "parallel_media",
              category_color: categoryHex,
              parallel_kind: "photo",
              parallel_source: pm.source || "pexels",
            },
          };
          items[pmItemId] = pmItem;
          videoTrackItemIds.push(pmItemId);
        }
      });
    }

    // ── Pure stock / generated blocks: stock_media_url fallback ────
    // For categories where the asset IS the block (not an overlay on top
    // of an avatar), the auto-populate writes to `stock_media_url` and
    // does NOT seed parallel_media. Without this fallback, those blocks
    // would arrive in the editor empty (the V1 avatar item is correctly
    // skipped because there's no avatar to render). Synthesize a single
    // full-canvas item from stock_media_url so the asset shows up.
    const blockCategory = (block as any).category as string | undefined;
    const stockMediaUrl = (block as any).stock_media_url as string | undefined;
    const stockMediaThumb = (block as any).stock_media_thumbnail as string | undefined;
    const stockMediaKind = ((block as any).stock_media_kind as "video" | "photo" | undefined) || "video";
    const isPureStockCategory =
      blockCategory === "stock_video" ||
      blockCategory === "stock_photo" ||
      blockCategory === "generated_photo" ||
      blockCategory === "generated_video";
    const hasParallelMedia = Array.isArray(parallelMedia) && parallelMedia.length > 0;
    if (isPureStockCategory && !hasParallelMedia && stockMediaUrl) {
      const sItemId = `stock_${block.id}`;
      const sAssetId = `asset_stock_${block.id}`;
      const itemFrom = secondsToFrames(start, fps);
      const itemFrames = Math.max(1, durFrames);
      if (stockMediaKind === "video") {
        const sAsset: VideoAsset = {
          type: "video",
          id: sAssetId,
          filename: `Stock \u2014 Block ${blockNum}`,
          size: 0,
          remoteUrl: stockMediaUrl,
          remoteFileKey: null,
          mimeType: "video/mp4",
          durationInSeconds: dur,
          hasAudioTrack: false,
          width: canvas.width,
          height: canvas.height,
        };
        assets[sAssetId] = sAsset;
        const sItem: VideoItem = {
          type: "video",
          id: sItemId,
          assetId: sAssetId,
          from: itemFrom,
          durationInFrames: itemFrames,
          top: 0,
          left: 0,
          width: canvas.width,
          height: canvas.height,
          opacity: 1,
          isDraggingInTimeline: false,
          keepAspectRatio: true,
          fadeInDurationInSeconds: 0,
          fadeOutDurationInSeconds: 0,
          borderRadius: 0,
          rotation: 0,
          cropLeft: 0,
          cropTop: 0,
          cropRight: 0,
          cropBottom: 0,
          videoStartFromInSeconds: 0,
          playbackRate: 1,
          decibelAdjustment: -60, // mute the stock clip's own audio
          audioFadeInDurationInSeconds: 0,
          audioFadeOutDurationInSeconds: 0,
          metadata: {
            block_id: block.id,
            track_type: "stock_media",
            category_color: categoryHex,
            stock_kind: "video",
          },
        };
        items[sItemId] = sItem;
        videoTrackItemIds.push(sItemId);
      } else {
        const sAsset: ImageAsset = {
          type: "image",
          id: sAssetId,
          filename: `Stock \u2014 Block ${blockNum}`,
          size: 0,
          remoteUrl: stockMediaThumb || stockMediaUrl,
          remoteFileKey: null,
          mimeType: "image/jpeg",
          width: canvas.width,
          height: canvas.height,
        };
        assets[sAssetId] = sAsset;
        const sItem: ImageItem = {
          type: "image",
          id: sItemId,
          assetId: sAssetId,
          from: itemFrom,
          durationInFrames: itemFrames,
          top: 0,
          left: 0,
          width: canvas.width,
          height: canvas.height,
          opacity: 1,
          isDraggingInTimeline: false,
          keepAspectRatio: true,
          fadeInDurationInSeconds: 0,
          fadeOutDurationInSeconds: 0,
          borderRadius: 0,
          rotation: 0,
          cropLeft: 0,
          cropTop: 0,
          cropRight: 0,
          cropBottom: 0,
          metadata: {
            block_id: block.id,
            track_type: "stock_media",
            category_color: categoryHex,
            stock_kind: "photo",
          },
        };
        items[sItemId] = sItem;
        videoTrackItemIds.push(sItemId);
      }
    }

    // Product image for this block. Two-stage resolution + two render modes.
    //
    // Source-of-truth order for `prod`:
    //   1. Block-level product_id (the user explicitly attached this product
    //      to this block).
    //   2. Round-robin from cast.products (the user attached products at the
    //      cast level but never picked which goes on which block).
    // Without the fallback, blocks with no product_id render with NO product
    // overlay — which is what the user reported: products attached via
    // cast_products but never visible in the final video.
    //
    // Render modes (chosen below):
    //   1. CAROUSEL — when block.metadata.product_carousel is true AND the
    //      attached product has 2+ assets, emit a sequence of full-canvas
    //      items that the FFmpeg composer chains with xfade. Each item
    //      carries metadata.carousel_group_id (== block_id) + index +
    //      transition + total_count so the composer can reassemble them.
    //   2. STATIC OVERLAY (default) — single 30%-of-canvas product chip in
    //      the bottom-right corner with the cover image. Pre-existing
    //      behaviour, preserved for blocks without product_carousel set.
    //
    // CRITICAL: when carousel mode is on, we SKIP the static overlay entirely
    // — otherwise both layers render simultaneously.
    let prod = block.product_id ? productMap.get(block.product_id) : undefined;
    if (!prod && castProducts.length > 0) {
      prod = castProducts[blockProductCursor % castProducts.length];
      blockProductCursor += 1;
    }
    const prodCoverUrl = prod?.cover_image_url || (prod?.cover_image_key ? cdnUrl(prod.cover_image_key) : "");
    const blockMeta = ((block as any).metadata || {}) as {
      product_carousel?: boolean;
      carousel_speed_seconds?: number;
      carousel_transition?: "crossfade" | "slide" | "zoom";
      carousel_asset_ids?: string[];
    };
    const allProdAssets = Array.isArray(prod?.assets) ? prod!.assets! : [];
    // Honour user-curated subset/order if set; otherwise use the full list
    // in the server's default order.
    const orderedAssets = blockMeta.carousel_asset_ids && blockMeta.carousel_asset_ids.length > 0
      ? blockMeta.carousel_asset_ids
          .map((id) => allProdAssets.find((a) => a.id === id))
          .filter((a): a is { id: string; r2_url: string; media_type: string } => !!a)
      : allProdAssets;

    const carouselActive = !!blockMeta.product_carousel && orderedAssets.length > 1;

    if (prod && carouselActive) {
      // Carousel emission. Math intentionally mirrors the FFmpeg composer:
      //   itemCount = ceil(blockDuration / speed)
      //   itemEnd   = min(itemStart + speed + 0.5, end)
      // The +0.5 leaves a half-second overhang for crossfade overlap on
      // mid-items; for the last item the min() clamp eats the overhang
      // (no item to fade INTO), which is fine.
      const speed = typeof blockMeta.carousel_speed_seconds === "number" && blockMeta.carousel_speed_seconds > 0
        ? blockMeta.carousel_speed_seconds
        : 3;
      const transition = blockMeta.carousel_transition || "crossfade";
      const blockDuration = dur;
      const itemCount = Math.max(1, Math.ceil(blockDuration / speed));

      for (let i = 0; i < itemCount; i++) {
        const asset = orderedAssets[i % orderedAssets.length];
        if (!asset) continue;  // defensive — orderedAssets.length > 1 guarantees this is non-empty
        const itemStart = start + i * speed;
        const itemEnd = Math.min(itemStart + speed + 0.5, end);
        const itemDur = Math.max(0.1, itemEnd - itemStart);

        const carouselItemId = `carousel_${block.id}_${i}`;
        const carouselAssetId = `asset_${carouselItemId}`;

        if (asset.media_type === "video") {
          const cAsset: VideoAsset = {
            type: "video",
            id: carouselAssetId,
            filename: `${prod.name || "Product"} — carousel ${i + 1}`,
            size: 0,
            remoteUrl: asset.r2_url,
            remoteFileKey: null,
            mimeType: "video/mp4",
            durationInSeconds: itemDur,
            hasAudioTrack: false,
            width: canvas.width,
            height: canvas.height,
          };
          assets[carouselAssetId] = cAsset;
          const cItem: VideoItem = {
            type: "video",
            id: carouselItemId,
            assetId: carouselAssetId,
            from: secondsToFrames(itemStart, fps),
            durationInFrames: Math.max(1, secondsToFrames(itemDur, fps)),
            top: 0,
            left: 0,
            width: canvas.width,
            height: canvas.height,
            opacity: 1,
            isDraggingInTimeline: false,
            videoStartFromInSeconds: 0,
            decibelAdjustment: -60,  // mute video audio — we want only the cast voiceover
            playbackRate: 1,
            audioFadeInDurationInSeconds: 0,
            audioFadeOutDurationInSeconds: 0,
            fadeInDurationInSeconds: 0,
            fadeOutDurationInSeconds: 0,
            keepAspectRatio: true,
            borderRadius: 0,
            rotation: 0,
            cropLeft: 0,
            cropTop: 0,
            cropRight: 0,
            cropBottom: 0,
            metadata: {
              block_id: block.id,
              track_type: "product",
              category_color: categoryHex,
              carousel_group_id: block.id,
              carousel_index: i,
              carousel_total: itemCount,
              carousel_transition: transition,
              carousel_speed_seconds: speed,
            },
          };
          items[carouselItemId] = cItem;
        } else {
          const cAsset: ImageAsset = {
            type: "image",
            id: carouselAssetId,
            filename: `${prod.name || "Product"} — carousel ${i + 1}`,
            size: 0,
            remoteUrl: asset.r2_url,
            remoteFileKey: null,
            mimeType: "image/jpeg",
            width: canvas.width,
            height: canvas.height,
          };
          assets[carouselAssetId] = cAsset;
          const cItem: ImageItem = {
            type: "image",
            id: carouselItemId,
            assetId: carouselAssetId,
            from: secondsToFrames(itemStart, fps),
            durationInFrames: Math.max(1, secondsToFrames(itemDur, fps)),
            top: 0,
            left: 0,
            width: canvas.width,
            height: canvas.height,
            opacity: 1,
            isDraggingInTimeline: false,
            keepAspectRatio: true,
            fadeInDurationInSeconds: 0,
            fadeOutDurationInSeconds: 0,
            borderRadius: 0,
            rotation: 0,
            cropLeft: 0,
            cropTop: 0,
            cropRight: 0,
            cropBottom: 0,
            metadata: {
              block_id: block.id,
              track_type: "product",
              category_color: categoryHex,
              carousel_group_id: block.id,
              carousel_index: i,
              carousel_total: itemCount,
              carousel_transition: transition,
              carousel_speed_seconds: speed,
            },
          };
          items[carouselItemId] = cItem;
        }
        // Products and carousel items share the same track so z-order
        // matches the existing static-overlay layer order.
        productTrackItemIds.push(carouselItemId);
      }
    } else if (prod && prodCoverUrl) {
      // Static overlay — pre-existing behaviour, preserved verbatim. Skipped
      // entirely when carouselActive so we don't double-render.
      const prodItemId = `prod_${block.id}`;
      const prodAssetId = `asset_prod_${block.id}`;
      const prodW = Math.round(canvas.width * 0.3);
      const prodH = Math.round(canvas.height * 0.3);
      const prodAsset: ImageAsset = {
        type: "image",
        id: prodAssetId,
        filename: prod.name || "Product",
        size: 0,
        remoteUrl: prodCoverUrl,
        remoteFileKey: null,
        mimeType: "image/jpeg",
        width: prodW,
        height: prodH,
      };
      assets[prodAssetId] = prodAsset;

      const prodItem: ImageItem = {
        type: "image",
        id: prodItemId,
        assetId: prodAssetId,
        from: secondsToFrames(start, fps),
        durationInFrames: durFrames,
        top: canvas.height - prodH - 20,
        left: canvas.width - prodW - 20,
        width: prodW,
        height: prodH,
        opacity: 1,
        isDraggingInTimeline: false,
        keepAspectRatio: true,
        fadeInDurationInSeconds: 0,
        fadeOutDurationInSeconds: 0,
        borderRadius: 8,
        rotation: 0,
        cropLeft: 0,
        cropTop: 0,
        cropRight: 0,
        cropBottom: 0,
        metadata: {
          block_id: block.id,
          track_type: "product",
          // Products always carry the parent block's category color so the
          // chip on the timeline visually groups with its block.
          category_color: categoryHex,
        },
      };
      items[prodItemId] = prodItem;
      productTrackItemIds.push(prodItemId);
    }

    // ── Caption items from word timestamps ──
    const rawCaptionWords = variant?.caption_words as Array<{word: string; start: number; end: number; score?: number}> | undefined;
    // Fallback: if Whisper didn't populate caption_words, evenly distribute script words
    let workingCaptions = rawCaptionWords;
    if ((!workingCaptions || workingCaptions.length === 0) && options.captionsEnabled !== false) {
      // Strip [sfx:*]/(emotion) direction markers before tokenizing, or they
      // leak into rendered captions.
      const script = stripScriptMarkers(variant?.script_text || "");
      const dur = end - start;
      if (script && dur > 0.5) {
        const words = script.split(/\s+/).filter(Boolean);
        const tpw = dur / Math.max(words.length, 1);
        workingCaptions = words.map((w: string, i: number) => ({
          word: w,
          start: Math.round(i * tpw * 1000) / 1000,
          end: Math.round((i + 1) * tpw * 1000) / 1000,
          score: 0.5,
        }));
      }
    }
    if (workingCaptions && workingCaptions.length > 0 && options.captionsEnabled !== false) {
      // ── FIX 1 (Caption UX overhaul) ────────────────────────────────
      // Use @remotion/captions properly: emit ONE CaptionsItem per block,
      // backed by ONE CaptionAsset whose `captions` array carries every
      // word-level token. The CaptionsLayer internally calls
      // createTikTokStyleCaptions() to group tokens into pages and renders
      // each page (with karaoke-style active-word highlighting) as a
      // <Sequence>. Result: the timeline shows ONE caption strip per block
      // (e.g. "Alright guys listen up I have ..."), not 30 separate word
      // fragments. Word-level data is preserved — just grouped at render
      // time by Remotion's captions API.
      const captionTokens: Caption[] = workingCaptions.map((w) => {
        const startMs = Math.round((w.start + start) * 1000);
        const endMs = Math.max(Math.round((w.end + start) * 1000), startMs + 1);
        return {
          text: w.word,
          startMs,
          endMs,
          timestampMs: Math.round((startMs + endMs) / 2),
          confidence: typeof w.score === "number" ? w.score : null,
        };
      });

      const firstStartS = captionTokens[0].startMs / 1000;
      const lastEndS = captionTokens[captionTokens.length - 1].endMs / 1000;
      const blockCaptionDurS = Math.max(lastEndS - firstStartS, 0.1);

      const capAssetId = `capasset_${block.id}`;
      const capAsset: CaptionAsset = {
        id: capAssetId,
        type: "caption",
        captions: captionTokens,
        filename: `captions_${block.id}.json`,
        remoteUrl: null,
        remoteFileKey: null,
        size: new Blob([JSON.stringify(captionTokens)]).size,
        mimeType: "application/json",
      };
      assets[capAssetId] = capAsset;

      // Caption box: bottom 15% of canvas, full “safe” width.
      const capWidth = Math.round(canvas.width * 0.85);
      const capHeight = 120;
      const capLeft = Math.round((canvas.width - capWidth) / 2);
      const capTop = Math.round(canvas.height * 0.78);

      const capItemId = `cap_${block.id}`;
      const capItem: CaptionsItem = {
        type: "captions",
        id: capItemId,
        assetId: capAssetId,
        from: secondsToFrames(firstStartS, fps),
        durationInFrames: Math.max(secondsToFrames(blockCaptionDurS, fps), 1),
        top: capTop,
        left: capLeft,
        width: capWidth,
        height: capHeight,
        opacity: 1,
        isDraggingInTimeline: false,
        rotation: 0,
        fontFamily: "Inter",
        fontStyle: { variant: "normal", weight: "700" },
        fontSize: 42,
        lineHeight: 1.2,
        letterSpacing: 0,
        align: "center",
        color: "#FFFFFF",
        highlightColor: "#FFD700",
        strokeWidth: 3,
        strokeColor: "#000000",
        direction: "ltr",
        // Group tokens into ~3.5s pages for the TikTok-style flow.
        pageDurationInMilliseconds: 3500,
        // Caption tokens carry ABSOLUTE timeline ms (startMs is already
        // shifted by `start` above). The captions-layer subtracts
        // `captionStartInSeconds * fps` from `(page.startMs / 1000) * fps`
        // to derive frames relative to the parent <Sequence from={item.from}>.
        // Without this, every caption block past the first rendered at
        // 2× its intended frame and fell outside the parent Sequence's
        // duration — only block 1 (whose item.from ≈ 0) ever showed up.
        captionStartInSeconds: firstStartS,
        maxLines: 2,
        fadeInDurationInSeconds: 0,
        fadeOutDurationInSeconds: 0,
        metadata: {
          block_id: block.id,
          track_type: "captions",
          // Captions inherit the parent block's category color for the
          // timeline indicator strip; the on-canvas text stays neutral.
          category_color: categoryHex,
          // Phase 2.6 metadata so per-block regeneration / locking still works.
          locked_to_source: true,
          stale: false,
          // Caption Fix 2 — default preset for fresh casts; the style bar
          // can re-apply any of the five built-in presets.
          caption_preset: "modern_pop",
        },
      };
      items[capItemId] = capItem;
      captionTrackItemIds.push(capItemId);
    }

    // ── SFX — resolved [sfx:NAME] markers become real audio accents ──
    // Regression 4 (see tests/unit/test_sfx_resolver.py): the marker was
    // only ever stripped from captions/TTS, never actually mixed in — the
    // backend's variant.sfx_timings was computed and stored correctly, but
    // the only code that ever turned it into a playable timeline element
    // was routers/casts/timeline.py's auto_arrange_cast_timeline, a
    // headless/API-only endpoint this app's own editor never calls. This
    // mirrors that backend logic directly in the path every real cast
    // actually goes through — same fix shape as the background-music one
    // just above the caption block.
    const sfxTimings = variant?.sfx_timings;
    if (sfxTimings && sfxTimings.length > 0) {
      sfxTimings.forEach((timing, ti) => {
        const name = (timing?.name || "").trim().toLowerCase();
        const entry = SFX_CATALOG[name];
        if (!entry) {
          console.warn(`Unknown SFX '${timing?.name}' in block ${block.id}; skipping`);
          return;
        }
        const relStart = Math.max(Number(timing.start_s) || 0, 0);
        const sfxStart = start + relStart;

        const sfxAssetId = `asset_sfx_${block.id}_${ti}`;
        const sfxAsset: AudioAsset = {
          type: "audio",
          id: sfxAssetId,
          filename: `SFX — ${name}`,
          size: 0,
          remoteUrl: sfxUrl(name),
          remoteFileKey: null,
          mimeType: "audio/wav",
          durationInSeconds: entry.durationS,
        };
        assets[sfxAssetId] = sfxAsset;

        const sfxItemId = `sfx_${block.id}_${ti}`;
        const sfxItem: AudioItem = {
          type: "audio",
          id: sfxItemId,
          assetId: sfxAssetId,
          from: secondsToFrames(sfxStart, fps),
          durationInFrames: Math.max(secondsToFrames(entry.durationS, fps), 1),
          top: 0,
          left: 0,
          width: 0,
          height: 0,
          opacity: 1,
          isDraggingInTimeline: false,
          audioStartFromInSeconds: 0,
          decibelAdjustment: 0,
          playbackRate: 1,
          audioFadeInDurationInSeconds: 0,
          audioFadeOutDurationInSeconds: 0,
          metadata: {
            block_id: block.id,
            track_type: "audio_sfx",
            kind: "sfx",
            name,
            volume: entry.volume,
          },
        };
        items[sfxItemId] = sfxItem;
        sfxTrackItemIds.push(sfxItemId);
      });
    }

    cursor = end;
  }

  // Auto-place background music as its own unbonded audio track spanning
  // the whole cast, mirroring what the backend's headless auto-arrange
  // endpoint already does (routers/casts/timeline.py) for API-driven
  // callers. This editor path — the one every real cast actually goes
  // through — never read cast.background_music_url at all, so attaching
  // a track (whether via the auto-generate flow or the Music page's
  // "+ Add") updated the cast row but never produced a timeline element,
  // and the renderer's music mixer had nothing to pick up. metadata.kind
  // "music" is what the composer's classifier keys off of to route this
  // to the ducked-under-voice music mix instead of treating it as a
  // second voice track.
  const musicChoice = (cast as any).music_track_choice || "auto";
  if (cast.background_music_url && musicChoice !== "off" && cursor > 0) {
    const musicAssetId = "asset_music_bg";
    const musicAsset: AudioAsset = {
      type: "audio",
      id: musicAssetId,
      filename: "Background music",
      size: 0,
      remoteUrl: cast.background_music_url,
      remoteFileKey: null,
      mimeType: "audio/mpeg",
      durationInSeconds: cursor,
    };
    assets[musicAssetId] = musicAsset;

    const musicItemId = "music_bg";
    const musicItem: AudioItem = {
      type: "audio",
      id: musicItemId,
      assetId: musicAssetId,
      from: 0,
      durationInFrames: secondsToFrames(cursor, fps),
      top: 0,
      left: 0,
      width: 0,
      height: 0,
      opacity: 1,
      isDraggingInTimeline: false,
      audioStartFromInSeconds: 0,
      decibelAdjustment: 0,
      playbackRate: 1,
      audioFadeInDurationInSeconds: 0,
      audioFadeOutDurationInSeconds: 0,
      metadata: {
        track_type: "audio_music",
        kind: "music",
        source: "auto",
      },
    };
    items[musicItemId] = musicItem;
    musicTrackItemIds.push(musicItemId);
  }

  // Compute total duration in frames
  const totalFrames = Math.max(secondsToFrames(cursor, fps), 1);

  // Track order is critical for visual stacking. The canvas renderer reverses
  // this array before drawing, so the FIRST entry here ends up on TOP. We
  // want, top to bottom:
  //    1. Captions (always readable over everything)
  //    2. Product images / chips
  //    3. Avatar (V1 video face)
  //    4. Audio (no visual, irrelevant for stacking)
  // Any other overlays the user adds default to the video track — placing
  // the avatar track below products keeps avatar from covering them and
  // matches the 'avatar in the back, product chip on top of avatar' layout
  // used in the renderer.
  const tracks: TrackType[] = [
    ...(captionTrackItemIds.length > 0
      ? [{ id: captionTrackId, items: captionTrackItemIds, hidden: false, muted: false }]
      : []),
    ...(productTrackItemIds.length > 0
      ? [{ id: productTrackId, items: productTrackItemIds, hidden: false, muted: false }]
      : []),
    { id: videoTrackId, items: videoTrackItemIds, hidden: false, muted: false },
    { id: audioTrackId, items: audioTrackItemIds, hidden: false, muted: false },
    ...(musicTrackItemIds.length > 0
      ? [{ id: musicTrackId, items: musicTrackItemIds, hidden: false, muted: false }]
      : []),
    ...(sfxTrackItemIds.length > 0
      ? [{ id: sfxTrackId, items: sfxTrackItemIds, hidden: false, muted: false }]
      : []),
  ];

  const state: UndoableState = {
    tracks,
    items,
    assets,
    fps,
    compositionWidth: canvas.width,
    compositionHeight: canvas.height,
    deletedAssets: [],
  };

  return { state, blockRegions: regions };
}

// ─── F.5.2 editorStarterToLuminacastSnapshot ──────────────────────

/**
 * Renderer-compatible snapshot shape.
 * This is what extract_bonded_blocks_from_timeline() in cast_render.py expects.
 */
export interface LuminacastSnapshot {
  tracks: LuminacastTrack[];
  compositionWidth: number;
  compositionHeight: number;
  fps: number;
  version: 1 | 2;
}

export interface LuminacastTrack {
  type: string;
  elements: LuminacastElement[];
}

export interface LuminacastElement {
  id: string;
  type: string;
  s: number;
  e: number;
  props: Record<string, any>;
  metadata?: {
    block_id?: string;
    bonded?: boolean;
    paired_audio_element_id?: string;
    paired_video_element_id?: string;
    [key: string]: any;
  };
}

/**
 * Convert Editor Starter UndoableState → renderer-compatible bonded V1/A1 snapshot.
 *
 * Iterates all items, resolves asset URLs, converts frames → seconds,
 * and groups into typed tracks that the backend renderer can parse.
 */
export function editorStarterToLuminacastSnapshot(undoableState: UndoableState): LuminacastSnapshot {
  const { items, assets, fps, tracks } = undoableState;

  const videoElements: LuminacastElement[] = [];
  const audioElements: LuminacastElement[] = [];

  // Iterate tracks to preserve ordering
  for (const track of tracks) {
    for (const itemId of track.items) {
      const item = items[itemId];
      if (!item) continue;

      const startSec = framesToSeconds(item.from, fps);
      const endSec = framesToSeconds(item.from + item.durationInFrames, fps);

      // Resolve asset URL
      let src = "";
      if ("assetId" in item && item.assetId) {
        const asset = assets[item.assetId];
        if (asset) {
          src = asset.remoteUrl || "";
        }
      }

      // Build props with position, size, and type-specific fields
      const props: Record<string, any> = { src };
      if ("top" in item) props.y = (item as any).top;
      if ("left" in item) props.x = (item as any).left;
      if ("width" in item) props.width = (item as any).width;
      if ("height" in item) props.height = (item as any).height;
      if ("opacity" in item) props.opacity = (item as any).opacity;
      if ("rotation" in item) props.rotation = (item as any).rotation;

      // ── Type-specific prop carry-over ──
      if (item.type === "text") {
        const ti = item as any;
        props.text = ti.text || "";
        props.fontSize = ti.fontSize;
        props.color = ti.color;
        props.fontColor = ti.color;
        props.fontFamily = ti.fontFamily;
        props.strokeWidth = ti.strokeWidth;
        props.strokeColor = ti.strokeColor;
        props.align = ti.align;
        props.textAlign = ti.align;
        props.background = ti.background;
        props.backgroundColor = ti.background;
      }

      if (item.type === "captions") {
        const ci = item as any;
        const asset = assets[ci.assetId];
        // Look up the preset (by metadata.caption_preset id) so its
        // FFmpeg-only hints (positionY, ffmpegBoxEnabled / ffmpegBoxColor,
        // textTransform) flow to the renderer alongside the basic
        // fontFamily/size/color/stroke fields. The renderer uses these
        // to approximate the preview's box backdrop, uppercase, and
        // vertical anchor.
        const presetIdMeta = (ci.metadata?.caption_preset as string | undefined) || null;
        const preset = presetIdMeta ? CAPTION_PRESETS[presetIdMeta] : null;
        // Editor stores tokens as Caption[] (Remotion):
        //   { text, startMs, endMs, timestampMs, confidence }
        // Backend renderer (Caption Fix 3 Phase A) pages them and emits
        // one FFmpeg drawtext window per page, so we forward the raw
        // ms timings instead of converting to seconds here.
        const tokensRaw: Array<any> = (asset as any)?.captions || [];
        const captionTokens = tokensRaw.map((c) => {
          if (typeof c?.startMs === "number") {
            return {
              text: c.text,
              startMs: c.startMs,
              endMs: c.endMs,
              timestampMs: c.timestampMs ?? null,
              confidence: c.confidence ?? null,
            };
          }
          // Legacy shape: {text, startInSeconds, endInSeconds}.
          return {
            text: c?.text ?? "",
            startMs: Math.round((c?.startInSeconds ?? 0) * 1000),
            endMs: Math.round((c?.endInSeconds ?? 0) * 1000),
            timestampMs: null,
            confidence: null,
          };
        });
        props.text = captionTokens.map((c: {text: string}) => c.text).join(" ");
        props.fontSize = ci.fontSize;
        props.color = ci.color;
        props.fontColor = ci.color;
        props.fontFamily = ci.fontFamily;
        props.fontWeight = Number(ci.fontStyle?.weight ?? 700);
        props.strokeWidth = ci.strokeWidth;
        props.strokeColor = ci.strokeColor;
        props.align = ci.align;
        props.textAlign = ci.align;
        props.highlightColor = ci.highlightColor;
        props.lineHeight = ci.lineHeight;
        props.letterSpacing = ci.letterSpacing;
        props.maxLines = ci.maxLines;
        props.pageDurationInMilliseconds = ci.pageDurationInMilliseconds;
        // Position on canvas — the renderer reads .top to derive the Y
        // baseline for drawtext.
        props.top = ci.top;
        props.left = ci.left;
        props.width = ci.width;
        props.height = ci.height;
        props.captionPreset = item.metadata?.caption_preset || null;
        props._captions_tokens = captionTokens;

        // Preset-driven FFmpeg hints. Falling back keeps legacy items
        // rendering with the same look they had before this PR.
        if (preset) {
          props.positionY = presetPositionFraction(preset);
          props.textTransform = preset.textTransform || "none";
          props.ffmpegBoxEnabled = !!preset.ffmpegBoxEnabled;
          props.ffmpegBoxColor = preset.ffmpegBoxColor || "black@0.5";
        }
      }

      if (item.type === "image") {
        const ii = item as any;
        props.fadeInDurationInSeconds = ii.fadeInDurationInSeconds ?? 0;
        props.fadeOutDurationInSeconds = ii.fadeOutDurationInSeconds ?? 0;
        props.borderRadius = ii.borderRadius ?? 0;
        props.rotation = ii.rotation ?? 0;
      }

      if (item.type === "video") {
        const vi = item as any;
        props.videoStartFromInSeconds = vi.videoStartFromInSeconds ?? 0;
        props.muted = !!vi.muted;
        props.decibelAdjustment = vi.decibelAdjustment ?? 0;
        props.playbackRate = vi.playbackRate ?? 1;
        props.borderRadius = vi.borderRadius ?? 0;
        props.fadeInDurationInSeconds = vi.fadeInDurationInSeconds ?? 0;
        props.fadeOutDurationInSeconds = vi.fadeOutDurationInSeconds ?? 0;
        props.keepAspectRatio = !!vi.keepAspectRatio;
        props.cropLeft = vi.cropLeft ?? 0;
        props.cropTop = vi.cropTop ?? 0;
        props.cropRight = vi.cropRight ?? 0;
        props.cropBottom = vi.cropBottom ?? 0;
      }

      if (item.type === "audio") {
        const ai = item as any;
        props.audioStartFromInSeconds = ai.audioStartFromInSeconds ?? 0;
        props.decibelAdjustment = ai.decibelAdjustment ?? 0;
        props.playbackRate = ai.playbackRate ?? 1;
        props.audioFadeInDurationInSeconds = ai.audioFadeInDurationInSeconds ?? 0;
        props.audioFadeOutDurationInSeconds = ai.audioFadeOutDurationInSeconds ?? 0;
      }

      if (item.type === "gif") {
        props.playbackRate = (item as any).playbackRate ?? 1;
      }

      if (item.type === "solid") {
        props.color = (item as any).color || "#000000";
      }

      const element: LuminacastElement = {
        id: item.id,
        type: item.type === "captions" ? "captions" : item.type,
        s: startSec,
        e: endSec,
        props,
        metadata: item.metadata ? { ...item.metadata } : undefined,
      };

      // Route every type, not just video/image/audio/text
      if (item.type === "video" || item.type === "image" || item.type === "gif" || item.type === "solid") {
        videoElements.push(element);
      } else if (item.type === "audio") {
        audioElements.push(element);
      } else if (item.type === "text" || item.type === "captions") {
        videoElements.push(element);
      }
    }
  }

  return {
    tracks: [
      { type: "video", elements: videoElements },
      { type: "audio", elements: audioElements },
    ],
    compositionWidth: undoableState.compositionWidth,
    compositionHeight: undoableState.compositionHeight,
    fps: undoableState.fps,
    version: 1,
  };
}

// ─── Helpers for auto-save (F.5.3) ──────────────────────

/**
 * Compute block regions from Editor Starter state.
 * Used to populate the block_regions field in the auto-save payload.
 */
export function computeBlockRegions(undoableState: UndoableState): BlockRegion[] {
  const { items, fps } = undoableState;
  const regions: BlockRegion[] = [];

  for (const item of Object.values(items)) {
    // Only count video/image items (V1) to avoid double-counting
    if (item.type !== "video" && item.type !== "image") continue;
    if (!item.metadata?.block_id || !item.metadata?.bonded) continue;

    regions.push({
      block_id: item.metadata.block_id,
      block_position: 0, // Position not stored in editor state; order comes from timeline
      variant_id: "", // Variant ID not stored in editor state
      start_s: framesToSeconds(item.from, fps),
      end_s: framesToSeconds(item.from + item.durationInFrames, fps),
    });
  }

  // Sort by start time
  regions.sort((a, b) => a.start_s - b.start_s);
  return regions;
}

/**
 * Load saved timeline or build fresh from cast data.
 * Loads saved timeline or builds fresh from cast data.
 */
export async function loadOrBuildEditorStarterTimeline(
  cast: Cast,
  options: CastToEditorOptions,
  fetchSaved: (variantId: string) => Promise<{ twick_data: any | null }>,
): Promise<{ state: UndoableState; blockRegions: BlockRegion[] }> {
  // Collect custom_labels from saved snapshot (if any) to restore after fresh build
  let savedCustomLabels: Record<string, string> = {};
  const firstVariant = cast.blocks?.[0]?.variants?.[0];
  if (firstVariant) {
    try {
      const saved = await fetchSaved(firstVariant.id);
      // twick_data contains a LuminacastSnapshot — extract custom_labels by element id
      const snapshot = saved?.twick_data as LuminacastSnapshot | null;
      if (snapshot?.tracks) {
        for (const track of snapshot.tracks) {
          for (const el of track.elements || []) {
            if (el.metadata?.custom_label) {
              savedCustomLabels[el.id] = el.metadata.custom_label;
            }
          }
        }
      }
    } catch {
      // Ignore fetch errors, build fresh
    }
  }

  const result = castToEditorStarterTimeline(cast, options);

  // Merge saved custom_labels back into fresh items
  if (Object.keys(savedCustomLabels).length > 0) {
    for (const [itemId, label] of Object.entries(savedCustomLabels)) {
      if (result.state.items[itemId]) {
        result.state.items[itemId] = {
          ...result.state.items[itemId],
          metadata: { ...result.state.items[itemId].metadata, custom_label: label },
        };
      }
    }
  }

  return result;
}
