/**
 * SafeZoneToggle — aspect-ratio guide selector in the editor toolbar.
 *
 * Per FIX 6, this is the radio cluster of "Group 2": only one platform's
 * safe-zone overlay is active at a time. Clicking the active chip again
 * deactivates it (so users can return to "no guides shown"). Visually
 * distinct from the toggle group to its right — radios get a filled
 * accent pill background, toggles get an underline.
 */
import React, { useCallback, useMemo } from "react";
import { PLATFORM_SAFE_ZONES, VERTICAL_PLATFORMS, HORIZONTAL_PLATFORMS } from "@/lib/safeZones";
import type { Platform } from "@/lib/safeZones";
import { useSafeZones } from "../safe-zone-context";
import { useDimensions } from "../utils/use-context";

export const SafeZoneToggle: React.FC = () => {
  const { activePlatforms, togglePlatform } = useSafeZones();
  const { compositionWidth, compositionHeight } = useDimensions();

  const visiblePlatforms = useMemo(() => {
    const isVertical = compositionHeight > compositionWidth;
    return isVertical ? VERTICAL_PLATFORMS : HORIZONTAL_PLATFORMS;
  }, [compositionWidth, compositionHeight]);

  // Radio behaviour: clicking a chip activates ONLY that platform (clearing
  // any others). Clicking the already-active chip clears all guides.
  const selectExclusive = useCallback(
    (platform: Platform) => {
      const isOnlyActive =
        activePlatforms.length === 1 && activePlatforms[0] === platform;
      if (isOnlyActive) {
        togglePlatform(platform); // turn off
        return;
      }
      // Clear everything else, then enable this one.
      for (const p of activePlatforms) {
        if (p !== platform) togglePlatform(p);
      }
      if (!activePlatforms.includes(platform)) togglePlatform(platform);
    },
    [activePlatforms, togglePlatform],
  );

  return (
    <div
      role="radiogroup"
      aria-label="Aspect ratio guides"
      className="flex shrink-0 items-center gap-1"
    >
      <span className="text-[10px] text-white/40 mr-1 select-none">Safe zones</span>
      <div className="flex items-center gap-0.5">
        {visiblePlatforms.map((platform) => {
          const config = PLATFORM_SAFE_ZONES[platform];
          const isActive = activePlatforms.includes(platform);
          return (
            <button
              key={platform}
              type="button"
              role="radio"
              aria-checked={isActive}
              onClick={() => selectExclusive(platform)}
              className={`flex h-7 items-center gap-1 rounded-md px-2 text-[11px] font-medium transition-colors ${
                isActive
                  ? "bg-white/10 text-accent"
                  : "text-white/50 hover:bg-white/5 hover:text-white/80"
              }`}
              title={`Show ${config.label} safe zones`}
            >
              {isActive && (
                <span
                  aria-hidden="true"
                  className="inline-block w-1.5 h-1.5 rounded-full"
                  style={{ backgroundColor: config.color.replace("0.15", "0.8") }}
                />
              )}
              {config.label}
            </button>
          );
        })}
      </div>
    </div>
  );
};
