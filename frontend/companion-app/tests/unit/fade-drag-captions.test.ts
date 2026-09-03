import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";
import {
  getCanFadeAudio,
  getCanFadeVisual,
} from "../../src/components/cast-builder/editor-starter/utils/fade";

/**
 * Dragging a caption clip's fade handle used to throw "expected fadeable
 * item" and crash the editor. Two things were wrong:
 *   1. use-fade-drag's pointer-up commit had a hand-rolled item-type
 *      allow-list that never learned about the 'captions' type.
 *   2. captions were marked visually fadeable at all — a whole-strip
 *      opacity fade fights the punchy per-word caption animation.
 *
 * Fix: captions are no longer visually fadeable (no handle, no fade curve),
 * and use-fade-drag validates via the canonical utils/fade maps.
 */
const ROOT = path.resolve(__dirname, "../../src/components/cast-builder/editor-starter");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8");

describe("fade capability maps", () => {
  const asItem = (type: string) => ({ type } as any);

  it("captions are NOT visually fadeable", () => {
    expect(getCanFadeVisual(asItem("captions"))).toBe(false);
    expect(getCanFadeAudio(asItem("captions"))).toBe(false);
  });

  it("the usual visual types still fade", () => {
    for (const t of ["video", "image", "text", "solid", "gif"]) {
      expect(getCanFadeVisual(asItem(t)), t).toBe(true);
    }
  });

  it("audio fade covers audio/video only", () => {
    expect(getCanFadeAudio(asItem("audio"))).toBe(true);
    expect(getCanFadeAudio(asItem("video"))).toBe(true);
  });
});

describe("fade UI is gated on getCanFadeVisual (so captions get no handle/curve)", () => {
  it("timeline-item renders the visual fade handles only when getCanFadeVisual", () => {
    const src = read("timeline/timeline-item/timeline-item.tsx");
    expect(src).toMatch(/getCanFadeVisual\(item\)\s*&&\s*FEATURE_VISUAL_FADE_CONTROL[\s\S]{0,200}fadeType="visual"/);
  });

  it("timeline-item-content renders the fade curve only when getCanFadeVisual", () => {
    const src = read("timeline/timeline-item/timeline-item-content.tsx");
    expect(src).toMatch(/getCanFadeVisual\(item\)\s*&&\s*FEATURE_VISUAL_FADE_CONTROL\s*&&\s*\(\s*<FadeCurve/);
  });

  it("use-fade-drag validates via the canonical maps, not a hard-coded list", () => {
    const src = read(
      "timeline/timeline-item/timeline-item-fade-control/use-fade-drag.ts",
    );
    expect(src).toContain("getCanFadeVisual(prevItem)");
    expect(src).toContain("getCanFadeAudio(prevItem)");
    expect(src).not.toContain("prevItem.type !== 'solid'");
  });
});
