/**
 * Source: Remotion Editor Starter v4.0.433, file: top-panel.tsx
 * Adapted for Luminacast: Added LuminacastMediaPanel as left sidebar
 * Phase 2.9.3: Added collapsible LayerOrderPanel toggled via Layers button
 */
import {PlayerRef} from "@remotion/player";
import React from "react";
import {Canvas} from "./canvas/canvas";
import {Inspector} from "./inspector/inspector";
import {LuminacastMediaPanel} from "./LuminacastMediaPanel";
import {useLoop} from "./utils/use-context";
import {useLayerPanel} from "./layer-panel-context";
import {LayerOrderPanel} from "./panels/LayerOrderPanel";

export const TopPanel: React.FC<{
  playerRef: React.RefObject<PlayerRef | null>;
}> = ({playerRef}) => {
  const loop = useLoop();
  const {isOpen: layerPanelOpen} = useLayerPanel();

  return (
    // min-h-[280px]: TimelineContainer below is shrink-0 (never shrinks
    // below its natural content height, which grows with track count) and
    // this is the only flex-1 sibling in Editor's column. Adapting this
    // template from full-viewport (h-screen) to fit inside CastBuilder's
    // layout means the available height can be too small for a
    // many-track timeline to take shrink-0's full natural height AND
    // leave any of the flex-1 remainder for this panel — without a floor
    // here, that remainder clamps to 0 and the canvas silently vanishes
    // (still renders internally, just at zero height/opacity, so no error
    // ever surfaces). This guarantees the canvas always gets a usable
    // minimum regardless of how tall the timeline grows.
    <div className="relative h-full w-full flex-1 min-h-[280px]">
      <div className="absolute flex h-full w-full flex-row">
        <LuminacastMediaPanel />
        <Canvas playerRef={playerRef} loop={loop} />
        <div className="flex flex-col border-l-editor-starter-border border-l-[1px]">
          {layerPanelOpen && (
            <div className="w-[350px] border-b border-white/10 bg-editor-starter-panel overflow-y-auto max-h-[40%]">
              <LayerOrderPanel />
            </div>
          )}
          <div className="flex-1 min-h-0">
            <Inspector />
          </div>
        </div>
      </div>
    </div>
  );
};
