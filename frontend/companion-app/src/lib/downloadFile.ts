/**
 * Force a real file download instead of navigating to/opening the URL.
 *
 * `<a download href="...">` (or the JS equivalent, `a.download = "..."`)
 * only actually triggers a download when the URL is same-origin — for a
 * CROSS-origin URL (our render CDN, media.luminacast.com, is always a
 * different origin from the app) browsers silently ignore the `download`
 * attribute entirely and just navigate to/open the resource instead,
 * which is exactly the reported bug: clicking "Download MP4" opened the
 * raw video URL in a new tab and played it in the browser's native player
 * instead of saving a file.
 *
 * The fix is to fetch the resource ourselves, turn it into a same-origin
 * `blob:` URL, and point the temporary anchor at THAT instead — `download`
 * is always honored for blob: URLs regardless of where the bytes actually
 * came from.
 */
export async function downloadFile(url: string, filename: string): Promise<void> {
  if (!url) return;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    try {
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } finally {
      // Give the browser a moment to actually start the download before
      // revoking — an immediate revoke can race the download start in
      // some browsers and silently cancel it.
      setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000);
    }
  } catch (err) {
    console.error("downloadFile failed, falling back to opening the URL:", err);
    // Best-effort fallback: at least get the user to the file if the fetch
    // itself failed (e.g. a transient network error) — same old behavior
    // as before this fix, better than doing nothing.
    window.open(url, "_blank", "noreferrer");
  }
}
