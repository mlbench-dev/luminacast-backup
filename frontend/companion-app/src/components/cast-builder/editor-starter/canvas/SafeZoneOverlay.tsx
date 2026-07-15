/**
 * SafeZoneOverlay — renders translucent overlay zones on the Remotion Player
 * preview showing where platform UI sits.
 *
 * Phase 2.8.2: Overlays are visual-only, pointer-events: none, and rendered
 * above all content as a positioning aid.
 */
import React from "react";
import type { Platform, SafeZone, PlatformSafeZoneConfig } from "@/lib/safeZones";
import { PLATFORM_SAFE_ZONES } from "@/lib/safeZones";

interface SafeZoneOverlayProps {
  /** Which platforms to show overlays for */
  activePlatforms: Platform[];
  /** Canvas composition width in pixels */
  compositionWidth: number;
  /** Canvas composition height in pixels */
  compositionHeight: number;
}

export const SafeZoneOverlay: React.FC<SafeZoneOverlayProps> = ({
  activePlatforms,
  compositionWidth,
  compositionHeight,
}) => {
  if (activePlatforms.length === 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: compositionWidth,
        height: compositionHeight,
        pointerEvents: "none",
        zIndex: 9999,
      }}
    >
      {activePlatforms.map((platform) => {
        const config = PLATFORM_SAFE_ZONES[platform];
        if (!config) return null;
        return (
          <React.Fragment key={platform}>
            {config.zones.map((zone, idx) => (
              <div
                key={`${platform}-${idx}`}
                style={{
                  position: "absolute",
                  top: `${zone.top}%`,
                  left: `${zone.left}%`,
                  width: `${zone.width}%`,
                  height: `${zone.height}%`,
                  backgroundColor: config.color,
                  borderBottom: zone.top === 0 ? `1px solid ${config.color.replace("0.15", "0.4")}` : undefined,
                  borderTop: zone.top > 0 ? `1px solid ${config.color.replace("0.15", "0.4")}` : undefined,
                }}
                title={`${config.label}: ${zone.name}`}
              />
            ))}
          </React.Fragment>
        );
      })}
    </div>
  );
};
