/**
 * Editor Starter module — Remotion-based editor for Luminacast Cast Builder.
 *
 * Source: Remotion Editor Starter v4.0.433
 * Integration: Strategy A (forked into Luminacast repo)
 *
 * Exports:
 * - LuminacastEditor: Main editor component, mountable within CastBuilder layout
 * - Editor state types for use in the mapping layer
 */

export { LuminacastEditor } from "./LuminacastEditor";
export type { LuminacastEditorProps } from "./LuminacastEditor";

// Re-export types needed by the mapping layer (Phase F.5)
export type { EditorState, UndoableState, TrackType } from "./state/types";
export type { EditorStarterItem } from "./items/item-type";
export type { BaseItem, ItemMetadata } from "./items/shared";
export type {
  EditorStarterAsset,
  ImageAsset,
  VideoAsset,
  AudioAsset,
  CaptionAsset,
} from "./assets/assets";

// Re-export state context for external state observation (auto-save bridge)
export { FullStateContext } from "./context-provider";
