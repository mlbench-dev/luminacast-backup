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
}

export const Editor: React.FC<EditorProps> = ({initialUndoableState, onUndoableStateChange}) => {
  const playerRef = useRef<PlayerRef | null>(null);

  return (
    <div className="bg-editor-starter-bg flex h-full w-full flex-col items-center justify-between">
      <SafeZoneProvider>
        <LayerPanelProvider>
        <ContextProvider initialUndoableState={initialUndoableState} onUndoableStateChange={onUndoableStateChange}>
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
