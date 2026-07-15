/**
 * Source: Remotion Editor Starter v4.0.433, file: rendering/format-relative-time.ts
 * Adapted for Luminacast: verbatim copy (utility function used by tasks indicator)
 */
export const formatRelativeTime = ({
  timestamp,
  now,
}: {
  timestamp: number;
  now: number;
}) => {
  if (typeof Intl === "undefined" || typeof Intl.RelativeTimeFormat === "undefined") {
    return null;
  }
  const diff = now - timestamp;
  const seconds = Math.round(diff / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (Math.abs(seconds) < 60) return rtf.format(-seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return rtf.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return rtf.format(-hours, "hour");
  const days = Math.round(hours / 24);
  return rtf.format(-days, "day");
};
