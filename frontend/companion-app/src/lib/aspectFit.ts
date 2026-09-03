/**
 * Aspect-fit pass for auto-built timelines.
 *
 * `castToEditorStarterTimeline` is a synchronous pure function — it has no
 * access to each asset's intrinsic pixel dimensions, so it places every
 * image/video item as a box the size of its target region (often the whole
 * frame). For background / b-roll that's fine: `object-fit: cover` fills the
 * frame and crops the overflow. For product shots it's wrong — a portrait
 * product photo forced into a 16:9 box is either cropped hard (preview) or
 * stretched (some render paths).
 *
 * This pass runs once, in the browser, right after the timeline is built:
 * for every item tagged `metadata.fit === "contain"` it probes the asset's
 * real dimensions and rewrites the item's box to the asset's aspect ratio,
 * centred inside the box the mapping originally gave it. The corrected
 * geometry is then auto-saved, so it's a one-time cost per cast.
 *
 * Items already carrying `metadata.aspect_fitted` are skipped (restored
 * saved timelines are already correct).
 */
import type { UndoableState } from "@/components/cast-builder/editor-starter/state/types";
import { fitElementSizeInContainer } from "@/components/cast-builder/editor-starter/utils/fit-element-size-in-container";

/** Per-session cache: asset URL → intrinsic dimensions (or null if unprobeable). */
const sizeCache = new Map<string, { width: number; height: number } | null>();

const PROBE_TIMEOUT_MS = 8000;

function probeImage(url: string): Promise<{ width: number; height: number } | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    const timer = setTimeout(() => {
      img.src = "";
      resolve(null);
    }, PROBE_TIMEOUT_MS);
    img.onload = () => {
      clearTimeout(timer);
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      resolve(w > 0 && h > 0 ? { width: w, height: h } : null);
    };
    img.onerror = () => {
      clearTimeout(timer);
      resolve(null);
    };
    img.src = url;
  });
}

function probeVideo(url: string): Promise<{ width: number; height: number } | null> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.crossOrigin = "anonymous";
    const cleanup = () => {
      video.removeAttribute("src");
      video.load();
    };
    const timer = setTimeout(() => {
      cleanup();
      resolve(null);
    }, PROBE_TIMEOUT_MS);
    video.onloadedmetadata = () => {
      clearTimeout(timer);
      const w = video.videoWidth;
      const h = video.videoHeight;
      cleanup();
      resolve(w > 0 && h > 0 ? { width: w, height: h } : null);
    };
    video.onerror = () => {
      clearTimeout(timer);
      cleanup();
      resolve(null);
    };
    video.src = url;
  });
}

async function probeMediaSize(
  url: string,
  kind: "image" | "video",
): Promise<{ width: number; height: number } | null> {
  if (!url) return null;
  if (sizeCache.has(url)) return sizeCache.get(url) ?? null;
  const result = kind === "video" ? await probeVideo(url) : await probeImage(url);
  sizeCache.set(url, result);
  return result;
}

/**
 * Rewrite the box of every `fit: "contain"` image/video item to the asset's
 * real aspect ratio, centred inside the box the mapping assigned it.
 * Returns the same state object (mutated in place) for call-site convenience.
 * Never throws — a failed probe leaves the item's box untouched (the
 * `object-fit: contain` / renderer `pad` fallback still prevents stretch).
 */
export async function applyAspectFit(state: UndoableState): Promise<UndoableState> {
  const targets = Object.values(state.items).filter(
    (item) =>
      (item.type === "image" || item.type === "video") &&
      item.metadata?.fit === "contain" &&
      !item.metadata?.aspect_fitted,
  );
  if (targets.length === 0) return state;

  await Promise.all(
    targets.map(async (item) => {
      const anyItem = item as {
        assetId?: string;
        left: number;
        top: number;
        width: number;
        height: number;
        metadata?: Record<string, unknown>;
      };
      const asset = anyItem.assetId ? state.assets[anyItem.assetId] : undefined;
      const url = asset?.remoteUrl || "";
      const natural = await probeMediaSize(url, item.type as "image" | "video");
      if (!natural) return;

      const boxW = anyItem.width;
      const boxH = anyItem.height;
      if (boxW <= 0 || boxH <= 0) return;

      const fitted = fitElementSizeInContainer({
        containerSize: { width: boxW, height: boxH },
        elementSize: natural,
      });

      anyItem.left = Math.round(anyItem.left + fitted.left);
      anyItem.top = Math.round(anyItem.top + fitted.top);
      anyItem.width = Math.round(fitted.width);
      anyItem.height = Math.round(fitted.height);
      anyItem.metadata = { ...(anyItem.metadata || {}), aspect_fitted: true };
    }),
  );

  return state;
}
