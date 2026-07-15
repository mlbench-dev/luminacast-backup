/**
 * SafeZoneContext — editor-local state for which platform safe zone
 * overlays are active. Not persisted to backend — purely a viewing aid.
 *
 * Phase 2.8.3
 */
import React, { createContext, useContext, useState, useCallback, useMemo } from "react";
import type { Platform } from "@/lib/safeZones";

interface SafeZoneState {
  activePlatforms: Platform[];
  togglePlatform: (platform: Platform) => void;
}

const SafeZoneContext = createContext<SafeZoneState>({
  activePlatforms: [],
  togglePlatform: () => {},
});

export const useSafeZones = () => useContext(SafeZoneContext);

export const SafeZoneProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activePlatforms, setActivePlatforms] = useState<Platform[]>([]);

  const togglePlatform = useCallback((platform: Platform) => {
    setActivePlatforms((prev) =>
      prev.includes(platform)
        ? prev.filter((p) => p !== platform)
        : [...prev, platform],
    );
  }, []);

  const value = useMemo(
    () => ({ activePlatforms, togglePlatform }),
    [activePlatforms, togglePlatform],
  );

  return (
    <SafeZoneContext.Provider value={value}>
      {children}
    </SafeZoneContext.Provider>
  );
};
