/**
 * SafeZoneWarning — shows an amber warning in the properties panel when
 * the selected element overlaps any active platform safe zone.
 *
 * Phase 2.8.4
 */
import React, { useMemo } from "react";
import { useSafeZones } from "../../safe-zone-context";
import { checkSafeZoneOverlap } from "@/lib/safeZones";
import { useDimensions } from "../../utils/use-context";

interface SafeZoneWarningProps {
  item: {
    left: number;
    top: number;
    width: number;
    height: number;
  };
}

export const SafeZoneWarning: React.FC<SafeZoneWarningProps> = ({ item }) => {
  const { activePlatforms } = useSafeZones();
  const { compositionWidth, compositionHeight } = useDimensions();

  const overlaps = useMemo(() => {
    if (activePlatforms.length === 0) return [];
    const all: string[] = [];
    for (const platform of activePlatforms) {
      const hits = checkSafeZoneOverlap(
        { x: item.left, y: item.top, width: item.width, height: item.height },
        compositionWidth,
        compositionHeight,
        platform,
      );
      all.push(...hits);
    }
    return all;
  }, [activePlatforms, item.left, item.top, item.width, item.height, compositionWidth, compositionHeight]);

  if (overlaps.length === 0) return null;

  return (
    <div className="mx-3 my-2 px-3 py-2 rounded-md bg-amber-500/10 border border-amber-500/20 text-amber-300 text-[11px]">
      <span className="font-medium">Safe zone overlap:</span>{" "}
      {overlaps.join(", ")}
    </div>
  );
};
