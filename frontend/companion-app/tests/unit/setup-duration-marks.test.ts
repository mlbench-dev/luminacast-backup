import { describe, it, expect } from "vitest";
import { computeDurationMarks } from "../../src/components/cast-builder/SetupPhase";

/**
 * Manual-duration slider tick marks.
 *
 * Bug: Quick/Standard pin the max to the template's est_duration_range
 * ([low, high]), but those values (e.g. 20s, 45s) aren't among the predefined
 * marks, so the tick row stopped at the last predefined mark under the cap
 * (Quick showed 5/15; Standard showed 5/15/30). The exact capped duration was
 * only reachable by dragging the slider to its end. computeDurationMarks now
 * appends the cap as its own labelled mark.
 */
describe("computeDurationMarks", () => {
  const RECORDED_MAX = 720;

  it("Quick: appends the low-end cap (20s) → 5, 15, 20", () => {
    expect(computeDurationMarks("seconds", false, 20, 20)).toEqual([5, 15, 20]);
  });

  it("Standard: appends the high-end cap (45s) → 5, 15, 30, 45", () => {
    expect(computeDurationMarks("seconds", false, 45, 45)).toEqual([5, 15, 30, 45]);
  });

  it("Premium (no tier cap): predefined marks up to the recorded ceiling, nothing appended", () => {
    expect(computeDurationMarks("seconds", false, RECORDED_MAX, undefined)).toEqual([
      5, 15, 30, 60, 120, 180, 300, 600, 720,
    ]);
  });

  it("does not duplicate a cap that already coincides with a predefined mark", () => {
    // range [15, 30] → Quick cap 15, Standard cap 30
    expect(computeDurationMarks("seconds", false, 15, 15)).toEqual([5, 15]);
    expect(computeDurationMarks("seconds", false, 30, 30)).toEqual([5, 15, 30]);
  });

  it("keeps the appended cap sorted when it lands between predefined marks", () => {
    // cap 45 sits between 30 and 60 — must not end up tacked on after 30 only
    const marks = computeDurationMarks("seconds", false, 45, 45);
    expect([...marks].sort((a, b) => a - b)).toEqual(marks);
  });

  it("minutes unit: cap is not appended (minutes can't represent a sub-60s cap)", () => {
    // The min toggle is disabled whenever a tier cap is active; guard matches.
    expect(computeDurationMarks("minutes", false, 45, 45)).toEqual([]);
  });

  it("LIVE (no tier cap) is unchanged — full 2-hour mark set", () => {
    expect(computeDurationMarks("seconds", true, 7200, undefined)).toEqual([
      60, 120, 300, 600, 900, 1800, 3600, 5400, 7200,
    ]);
  });
});
