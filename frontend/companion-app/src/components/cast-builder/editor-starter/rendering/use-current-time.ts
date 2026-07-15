/**
 * Source: Remotion Editor Starter v4.0.433, file: rendering/use-current-time.ts
 * Adapted for Luminacast: verbatim copy (utility hook)
 */
import { useEffect, useState } from "react";

export const useCurrentTime = () => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 10000);
    return () => clearInterval(interval);
  }, []);
  return Math.max(now, Date.now());
};
