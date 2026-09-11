/**
 * Source: Remotion Editor Starter v4.0.433, file: items/shared.ts
 * Adapted for Luminacast: Added optional metadata field to BaseItem for block_id persistence.
 *
 * The metadata field is used by the Luminacast mapping layer to track bonded
 * video+audio pairs through timeline edits. Because all state mutations use the
 * spread operator ({...item}), the metadata field is automatically preserved
 * through drag, trim, split, duplicate, and paste operations.
 */

export type ItemMetadata = {
  block_id?: string;
  bonded?: boolean;
  /**
   * FIX 5 — shared identifier for the two halves of a bonded pair (avatar
   * video + voice audio of the same block). Both items carry the same
   * `bonded_pair_id`, which lets the timeline correlate them at runtime
   * without relying on a directional pointer.
   */
  bonded_pair_id?: string;
  paired_audio_element_id?: string;
  paired_video_element_id?: string;
  /** Phase 2.1 — active variant id for bonded audio elements */
  variant_id?: string;
  /** Phase 2.6 — captions: the timeline audio element id this caption was generated from */
  source_audio_id?: string;
  /** Phase 2.6 — captions: auto-regenerate when source audio changes (default true) */
  locked_to_source?: boolean;
  /** Phase 2.6 — captions: true when source audio changed but caption not yet regenerated */
  stale?: boolean;
  /** Phase 2.6 — non-bonded items linked to a specific block for shift tracking */
  linked_to_block?: string;
  /** Phase 2.7 — visual element duration mode: how element responds to bonded audio changes */
  duration_mode?: "crop" | "stretch" | "keep";
  /**
   * How a mismatched-aspect image/video fills its box.
   *  - "cover"       (default when absent): fill the box, crop the overflow —
   *                  right only when the source is already close to the
   *                  box's own shape (a little edge trim is invisible).
   *  - "contain":     fit the whole asset inside the box, centred, letting
   *                  whatever's BEHIND it show through the margin — right
   *                  for product shots, and for b-roll cutaways layered on
   *                  top of a still-visible avatar clip.
   *  - "contain-blur": fit the whole asset inside the box, centred, and
   *                  fill the margin with a blurred, cover-fit copy of the
   *                  SAME source instead of exposing whatever's behind —
   *                  right for FULL-CANVAS primary content (stock/generated
   *                  b-roll, a voiceover block's visual) where nothing
   *                  meaningful sits behind it, so "contain" alone would
   *                  show a black/empty gap. Mirrors services.aspect_conform
   *                  on the render backend, so the preview matches what
   *                  actually renders instead of looking MORE zoomed-in
   *                  than the real output.
   * Read by croppable-layer.ts for the preview, carried through
   * editorStarterToLuminacastSnapshot() as props.fit for the renderer
   * ("contain-blur" collapses to "contain" there — see that mapping).
   */
  fit?: "cover" | "contain" | "contain-blur";
  /**
   * Set by ArrangePhase's applyAspectFit() pass once this item's
   * width/height/left/top have been re-fitted to the asset's real
   * intrinsic aspect ratio (probed in the browser). Guards against
   * re-probing an item that's already correct on subsequent editor opens.
   */
  aspect_fitted?: boolean;
  /**
   * Caption Fix 2 — the active caption preset id applied to this item.
   * Accepts any id from the canonical 15-preset module (`@/lib/captionPresets`);
   * the legacy 5-literal union was stale and rejected valid canonical ids.
   */
  caption_preset?: string;
  /**
   * True when this image is standing in for avatar video that hasn't been
   * generated yet (that only happens at Finalize & Render) — ImageLayer
   * badges the frame so a static photo isn't mistaken for real footage.
   */
  is_motion_placeholder?: boolean;
  /**
   * Per-ITEM visibility, distinct from TrackType.hidden (which hides every
   * item on a shared track — e.g. every block's B-roll or captions at
   * once). The Layers panel's per-row eye button used to call the
   * track-level hide action even though it's displayed per item, so
   * hiding one block's B-roll silently hid every other block's B-roll on
   * the same shared track too. This flag lets one specific item hide
   * without affecting anything else on its track.
   */
  hidden?: boolean;
  [key: string]: any;
};

export type BaseItem = {
  id: string;
  durationInFrames: number;
  from: number;
  top: number;
  left: number;
  width: number;
  height: number;
  opacity: number;
  isDraggingInTimeline: boolean;
  metadata?: ItemMetadata;
};

export type CanHaveBorderRadius = BaseItem & {
  borderRadius: number;
};

export type CanHaveRotation = BaseItem & {
  rotation: number;
};

export type CanHaveCrop = {
  cropLeft: number;
  cropTop: number;
  cropRight: number;
  cropBottom: number;
};
