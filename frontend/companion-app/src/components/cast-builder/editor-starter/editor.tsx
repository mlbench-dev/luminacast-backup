/**
 * Source: Remotion Editor Starter v4.0.433, file: editor.tsx
 * Adapted for Luminacast: Removed h-screen/w-screen, fits within CastBuilder layout.
 * Removed DownloadRemoteAssets and UseLocalCachedAssets (FEATURE_CACHE_ASSETS_LOCALLY=false).
 */

import type {PlayerRef} from "@remotion/player";
import {useRef} from "react";
import {Toaster} from "sonner";
import {ActionRow} from "./action-row/action-row";
import {CaptionStyleBar} from "./captioning/caption-style-bar";
import {ContextProvider} from "./context-provider";
import {FEATURE_RESIZE_TIMELINE_PANEL} from "./flags";
import {ForceSpecificCursor} from "./force-specific-cursor";
import {PlaybackControls} from "./playback-controls";
import {PreviewSizeProvider} from "./preview-size-provider";
import {TimelineResizer} from "./timeline-resizer";
import {Timeline} from "./timeline/timeline";
import {TimelineContainer} from "./timeline/timeline-container";
import {TopPanel} from "./top-panel";
import {WaitForInitialized} from "./wait-for-initialized";

import type {UndoableState} from './state/types';
import {SafeZoneProvider} from './safe-zone-context';
import {LayerPanelProvider} from './layer-panel-context';

export interface EditorProps {
  /** Optional initial undoable state — injected by ArrangePhaseRemotion for cast timeline data */
  initialUndoableState?: UndoableState;
  /** Callback fired when undoableState changes — used for auto-save */
  onUndoableStateChange?: (undoableState: UndoableState) => void;
  /** Live background-music bed volume (0..1) — see ContextProvider. */
  musicVolume?: number;
  /** Externally-supplied player ref — lets a parent (LuminacastEditor, then
   * ArrangePhase) reach in and e.g. pause() the preview from outside this
   * component, such as when a render starts and the editor goes read-only
   * (the preview would otherwise keep playing with no way to stop it — the
   * whole subtree, transport controls included, is inert while locked).
   * Falls back to an internally-created ref so every other Editor mount
   * (nothing else in this repo passes one today) is unaffected. */
  playerRef?: React.RefObject<PlayerRef | null>;
}

export const Editor: React.FC<EditorProps> = ({initialUndoableState, onUndoableStateChange, musicVolume, playerRef: externalPlayerRef}) => {
  const internalPlayerRef = useRef<PlayerRef | null>(null);
  const playerRef = externalPlayerRef ?? internalPlayerRef;

  return (
    <div className="bg-editor-starter-bg flex h-full w-full flex-col items-center justify-between">
      <SafeZoneProvider>
        <LayerPanelProvider>
        <ContextProvider initialUndoableState={initialUndoableState} onUndoableStateChange={onUndoableStateChange} musicVolume={musicVolume}>
          <WaitForInitialized>
            <PreviewSizeProvider>
              <ActionRow playerRef={playerRef} />
              <TopPanel playerRef={playerRef} />
            </PreviewSizeProvider>
            <PlaybackControls playerRef={playerRef} />
            {FEATURE_RESIZE_TIMELINE_PANEL && <TimelineResizer />}
            {/* Caption style bar (Fix 2) — visible only when the cast has
                caption items. Lets the user pick a preset and apply it to
                every caption at once. */}
            <CaptionStyleBar />
            <TimelineContainer playerRef={playerRef}>
              <Timeline playerRef={playerRef} />
            </TimelineContainer>
          </WaitForInitialized>
          <ForceSpecificCursor />
          <Toaster theme="dark" />
        </ContextProvider>
        </LayerPanelProvider>
      </SafeZoneProvider>
    </div>
  );
};
