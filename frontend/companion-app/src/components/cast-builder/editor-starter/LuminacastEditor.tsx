/**
 * LuminacastEditor — wrapper component that mounts the Remotion Editor Starter
 * within the Luminacast Cast Builder layout.
 *
 * This component:
 * 1. Accepts an optional initialUndoableState prop for loading cast timeline data
 * 2. Fires onUndoableStateChange when the editor state changes (for auto-save)
 * 3. Removes the Editor Starter chrome (h-screen/w-screen) to fit within CastBuilder
 * 4. Uses Luminacast design tokens via CSS variable overrides
 * 5. Provides LuminacastEditorContext so deep components can access castId/cast
 *
 * Usage in ArrangePhase:
 *   <LuminacastEditor
 *     cast={cast}
 *     initialUndoableState={state}
 *     onUndoableStateChange={(s) => debouncedSave(s)}
 *   />
 */
import React, { useMemo } from "react";
import { Editor } from "./editor";
import type { UndoableState } from "./state/types";
import type { Cast } from "@/lib/types";
import { LuminacastEditorContext } from "./luminacast-context";
// Pull in preset webfonts (Montserrat 800/900, Anton, Bebas Neue, Caveat,
// Playfair, Bangers, Comic Neue, Indie Flower) so the canvas preview
// matches the burnt-in MP4. Inter is loaded globally elsewhere.
import "@/lib/captionFonts";

export interface LuminacastEditorProps {
  /** The cast being edited — provides castId and block data to deep components */
  cast?: Cast;
  /** Optional class name for the editor container */
  className?: string;
  /** Optional initial timeline state — injected by ArrangePhaseRemotion */
  initialUndoableState?: UndoableState;
  /** Callback fired when undoableState changes — used for auto-save */
  onUndoableStateChange?: (undoableState: UndoableState) => void;
  /** Live background-music bed volume (0..1) from the Arrange-tab slider.
   *  The ContextProvider reconciles the timeline's music item(s) to this
   *  without remounting, so the preview volume changes during playback. */
  musicVolume?: number;
}

export const LuminacastEditor: React.FC<LuminacastEditorProps> = ({
  cast,
  className = "",
  initialUndoableState,
  onUndoableStateChange,
  musicVolume,
}) => {
  const ctxValue = useMemo(
    () => (cast ? { castId: cast.id, cast } : null),
    [cast],
  );

  const editor = (
    <div className={`flex flex-col h-full w-full ${className}`}>
      <Editor
        initialUndoableState={initialUndoableState}
        onUndoableStateChange={onUndoableStateChange}
        musicVolume={musicVolume}
      />
    </div>
  );

  if (ctxValue) {
    return (
      <LuminacastEditorContext.Provider value={ctxValue}>
        {editor}
      </LuminacastEditorContext.Provider>
    );
  }

  return editor;
};
