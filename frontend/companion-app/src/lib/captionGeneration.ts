/**
 * captionGeneration.ts — Phase 2.6.3
 *
 * Multi-block caption generation via backend Whisper proxy.
 *
 * Flow:
 *  1. Caller provides bonded audio elements (with block_ids) to generate captions for.
 *  2. For each audio element, we collect its audio URL and timeline offset.
 *  3. POST to /api/casts/{cast_id}/editor-generate-captions with all segments.
 *  4. Backend calls GPU Whisper with word-level timestamps per segment.
 *  5. We receive Remotion-compatible Caption tokens per block.
 *  6. We create CaptionAsset + CaptionsItem per block on the timeline.
 *
 * All caption items get metadata:
 *   - source_audio_id: the audio element they were generated from
 *   - locked_to_source: true (default — auto-regenerate when audio changes)
 *   - block_id: the block this caption belongs to
 *   - stale: false (fresh caption)
 */

import type { Caption } from "@remotion/captions";
import type { EditorStarterItem } from "@/components/cast-builder/editor-starter/items/item-type";
import type { AudioItem } from "@/components/cast-builder/editor-starter/items/audio/audio-item-type";
import type { EditorState } from "@/components/cast-builder/editor-starter/state/types";
import type { SetState } from "@/components/cast-builder/editor-starter/context-provider";
import type { EditorStarterAsset } from "@/components/cast-builder/editor-starter/assets/assets";
import { addCaptionAsset } from "@/components/cast-builder/editor-starter/state/actions/add-caption-asset";
import { addItem } from "@/components/cast-builder/editor-starter/state/actions/add-item";
import { generateRandomId } from "@/components/cast-builder/editor-starter/utils/generate-random-id";
import { castsApi } from "@/lib/api";

export interface CaptionGenerationSegment {
  /** The timeline audio item */
  audioItem: AudioItem;
  /** Resolved audio URL for this element */
  audioUrl: string;
  /** The block_id from metadata */
  blockId: string;
  /**
   * The Variant row this audio belongs to. When present, the backend
   * persists the transcription onto Variant.caption_words/caption_segments
   * (the render pipeline's actual source of truth) instead of only
   * returning tokens for the editor preview.
   */
  variantId?: string;
}

export interface CaptionGenerationResult {
  blockId: string;
  audioElementId: string;
  captions: Caption[];
  wordCount: number;
  error?: string;
}

/**
 * Generate captions for one or more bonded audio elements.
 *
 * Multi-select: caller passes multiple segments. Backend concatenates
 * via single Whisper call per segment and remaps timings per block.
 */
export async function generateCaptionsForBlocks(
  castId: string,
  segments: CaptionGenerationSegment[],
  fps: number,
): Promise<CaptionGenerationResult[]> {
  if (segments.length === 0) return [];

  // Build the request body: each segment needs audio_url, block_id,
  // audio_element_id, and start_offset_s (timeline start of this audio)
  const audioSegments = segments.map((seg) => ({
    audio_url: seg.audioUrl,
    block_id: seg.blockId,
    audio_element_id: seg.audioItem.id,
    start_offset_s: 0, // Whisper runs on each audio individually; offset is 0 per segment
    ...(seg.variantId ? { variant_id: seg.variantId } : {}),
  }));

  const response = await castsApi.editorGenerateCaptions(castId, audioSegments);

  return response.results.map((r) => ({
    blockId: r.block_id,
    audioElementId: r.audio_element_id,
    captions: r.captions.map((c) => ({
      text: c.text,
      startMs: c.startMs,
      endMs: c.endMs,
      timestampMs: c.timestampMs,
      confidence: c.confidence,
    })),
    wordCount: r.word_count,
    error: r.error,
  }));
}

/**
 * Apply caption results to the editor state: create CaptionAsset + CaptionsItem
 * per block on the timeline, with proper source linkage metadata.
 */
export function applyCaptionResultsToState(
  state: EditorState,
  results: CaptionGenerationResult[],
  segments: CaptionGenerationSegment[],
  fps: number,
  compositionWidth: number,
  compositionHeight: number,
): EditorState {
  let currentState = state;

  for (const result of results) {
    if (result.error || result.captions.length === 0) continue;

    // Find the matching segment to get the audio item position
    const seg = segments.find((s) => s.blockId === result.blockId);
    if (!seg) continue;

    const audioItem = seg.audioItem;

    // Create caption asset
    const { state: stateWithAsset, asset: captionAsset } = addCaptionAsset({
      state: currentState,
      captions: result.captions,
      filename: `captions_${result.blockId}.srt`,
    });
    currentState = stateWithAsset;

    // Compute caption duration: from first word to last word
    const firstCaption = result.captions[0];
    const lastCaption = result.captions[result.captions.length - 1];
    const captionDurationMs = (lastCaption?.endMs ?? 0) - (firstCaption?.startMs ?? 0);
    const captionDurationFrames = Math.max(
      Math.round((captionDurationMs / 1000) * fps),
      1,
    );

    // Caption placement defaults
    const defaultFontSize = 80;
    const defaultLineHeight = 1.2;
    const defaultMaxLines = 2;
    const width = Math.min(compositionWidth, 900) - 40;

    const captionItemId = generateRandomId();
    const captionItem = {
      type: "captions" as const,
      id: captionItemId,
      assetId: captionAsset.id,
      durationInFrames: captionDurationFrames,
      from: audioItem.from,
      height: defaultFontSize * defaultLineHeight * defaultMaxLines,
      width,
      left: (compositionWidth - width) / 2,
      top: compositionHeight / 2 + 150,
      opacity: 1,
      isDraggingInTimeline: false,
      fontFamily: "TikTok Sans",
      fontStyle: { variant: "normal" as const, weight: "600" },
      rotation: 0,
      lineHeight: defaultLineHeight,
      letterSpacing: 0,
      fontSize: defaultFontSize,
      align: "center" as const,
      color: "white",
      highlightColor: "#39E508",
      direction: "ltr" as const,
      pageDurationInMilliseconds: 2000,
      captionStartInSeconds: 0,
      maxLines: defaultMaxLines,
      fadeInDurationInSeconds: 0,
      fadeOutDurationInSeconds: 0,
      strokeWidth: 4,
      strokeColor: "black",
      // Phase 2.6 metadata: source linkage
      metadata: {
        source_audio_id: audioItem.id,
        locked_to_source: true,
        block_id: result.blockId,
        stale: false,
      },
    };

    // Find the track containing the audio item to place caption above it
    const tracks = currentState.undoableState.tracks;
    const audioTrackIndex = tracks.findIndex((track) =>
      track.items.some((itemId) => itemId === audioItem.id),
    );

    currentState = addItem({
      state: currentState,
      item: captionItem,
      select: false,
      position:
        audioTrackIndex >= 0
          ? { type: "directly-above", trackIndex: audioTrackIndex }
          : { type: "front" },
    });
  }

  return currentState;
}

/**
 * Single-block caption regeneration: used by auto-regeneration on bonded chain shift.
 * Generates captions for a single audio element and replaces existing caption items.
 */
export async function regenerateCaptionForBlock(
  castId: string,
  audioItem: AudioItem,
  audioUrl: string,
  blockId: string,
  fps: number,
  variantId?: string,
): Promise<CaptionGenerationResult | null> {
  const results = await generateCaptionsForBlocks(
    castId,
    [{ audioItem, audioUrl, blockId, variantId }],
    fps,
  );
  return results[0] ?? null;
}
