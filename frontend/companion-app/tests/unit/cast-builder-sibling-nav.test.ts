import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

/**
 * Source guard: navigating straight from one cast to a sibling (the
 * "This cast has a vertical version → Open" banner, or "Duplicate as …")
 * is a param-only route change. React keeps the same phase component
 * mounted and its load effects don't fully re-seed — the editor kept
 * showing the previous cast's timeline while the URL had already changed.
 *
 * Each cast-scoped phase component must be keyed on the cast id so it
 * remounts cleanly per cast.
 */
const SRC = path.resolve(__dirname, "../../src");
const cb = readFileSync(
  path.join(SRC, "pages/cast-builder/CastBuilder.tsx"),
  "utf8",
);

describe("CastBuilder — per-cast remount on sibling navigation", () => {
  it("keys ArrangePhase on the cast id", () => {
    expect(cb).toMatch(/<ArrangePhase\s+key=\{cast\.id\}/);
  });

  it("keys ScriptPhase on the cast id", () => {
    expect(cb).toMatch(/<ScriptPhase\s*\n?\s*key=\{cast\.id\}/);
  });

  it("keys ReadyPhase on the cast id", () => {
    expect(cb).toMatch(/<ReadyPhase\s+key=\{cast\.id\}/);
  });

  it("keys SetupPhase (cast may be null → falls back to a stable key)", () => {
    expect(cb).toMatch(/<SetupPhase\s*\n?\s*key=\{cast\?\.id \?\? "new"\}/);
  });
});
