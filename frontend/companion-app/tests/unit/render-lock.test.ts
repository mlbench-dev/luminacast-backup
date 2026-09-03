import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

/**
 * Source guard: while a render is queued/baking/composing, the Setup, Script
 * AND Arrange (timeline editor) tabs must be read-only so a mid-render edit
 * can't race the render task's live reads. The three phases share one pattern:
 * take a `renderInProgress` prop, show <RenderLockBanner>, and wrap their
 * content in `inert={renderInProgress}`. CastBuilder feeds all three the same
 * `renderStatus.status === "rendering"` + `handleCancelRender`.
 *
 * ArrangePhase was the gap — it stayed editable during render.
 */
const SRC = path.resolve(__dirname, "../../src");
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8");

const PHASE_FILES = {
  Setup: "components/cast-builder/SetupPhase.tsx",
  Script: "components/cast-builder/ScriptPhase.tsx",
  Arrange: "components/cast-builder/ArrangePhase.tsx",
} as const;

describe("render lock — read-only tabs during render", () => {
  for (const [name, rel] of Object.entries(PHASE_FILES)) {
    describe(`${name}Phase`, () => {
      const src = read(rel);

      it("imports RenderLockBanner", () => {
        expect(src).toMatch(/import\s*{\s*RenderLockBanner\s*}\s*from/);
      });

      it("accepts a renderInProgress prop", () => {
        expect(src).toMatch(/renderInProgress\??:\s*boolean/);
      });

      it("renders the lock banner when renderInProgress", () => {
        expect(src).toMatch(/renderInProgress\s*&&\s*<RenderLockBanner/);
      });

      it("makes its content inert while rendering", () => {
        expect(src).toContain("inert={renderInProgress}");
      });
    });
  }

  it("CastBuilder passes render state to all three phases", () => {
    const cb = read("pages/cast-builder/CastBuilder.tsx");
    // one occurrence per phase (Setup, Script, Arrange)
    const propHits = cb.match(/renderInProgress=\{renderStatus\.status === "rendering"\}/g) || [];
    expect(propHits.length).toBeGreaterThanOrEqual(3);
    const cancelHits = cb.match(/onCancelRender=\{handleCancelRender\}/g) || [];
    expect(cancelHits.length).toBeGreaterThanOrEqual(3);
    // and specifically on the ArrangePhase element
    expect(cb).toMatch(/<ArrangePhase[^>]*renderInProgress=\{renderStatus\.status === "rendering"\}/s);
  });
});
