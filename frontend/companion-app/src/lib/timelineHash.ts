/**
 * Compute a deterministic content hash of timeline tracks.
 * Must match the Python `_timeline_hash` in casts.py exactly:
 *   SHA-256 of JSON.stringify(tracks, sort_keys) → first 16 hex chars.
 */

function sortObjectKeys(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(sortObjectKeys);
  if (obj !== null && typeof obj === "object") {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(obj as Record<string, unknown>).sort()) {
      sorted[key] = sortObjectKeys((obj as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return obj;
}

export async function computeTimelineHash(tracks: unknown[]): Promise<string> {
  const canonical = JSON.stringify(sortObjectKeys(tracks));
  const data = new TextEncoder().encode(canonical);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}
