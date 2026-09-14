import { describe, it, expect } from "vitest";
import { friendlyBlockError } from "../RenderStatusPill";

/**
 * Bug: "the info tooltip you added when render fails still says something
 * went wrong instead of actual error." Root cause: friendlyBlockError only
 * recognized 3 specific error categories and collapsed EVERY other error —
 * including a perfectly real, specific one — into the same generic
 * "Something went wrong" text, hiding the actual reason even when we had
 * it. Now shown by default is the actual error; only the genuinely blank
 * case (which the backend was separately fixed to make rare/never) still
 * falls back to a generic line.
 */
describe("friendlyBlockError", () => {
  it("still translates the 3 recognized categories to plain language", () => {
    expect(friendlyBlockError("ClipValidationError: frozen")).toBe(
      "The generated clip didn't pass quality checks — try again.",
    );
    expect(friendlyBlockError("SpeakingBlockOutOfTolerance: 4.2s vs 6.8s")).toBe(
      "The voiceover doesn't match this clip's length — try again.",
    );
    expect(friendlyBlockError("AllProvidersFailedError: every tier failed")).toBe(
      "The video service didn't respond in time — try again.",
    );
    expect(friendlyBlockError("TimeoutError: connection reset")).toBe(
      "The video service didn't respond in time — try again.",
    );
  });

  it("shows the actual error text for anything it doesn't specifically recognize", () => {
    expect(friendlyBlockError("ValueError: invalid duration -3.0")).toBe(
      "ValueError: invalid duration -3.0",
    );
    expect(friendlyBlockError("ConnectionError: fal.ai unreachable")).toBe(
      "ConnectionError: fal.ai unreachable",
    );
  });

  it("truncates a very long raw error instead of showing it in full", () => {
    const long = "IntegrityError: " + "x".repeat(500);
    const out = friendlyBlockError(long);
    expect(out.length).toBeLessThan(long.length);
    expect(out.endsWith("…")).toBe(true);
    expect(out.startsWith("IntegrityError:")).toBe(true);
  });

  it("only falls back to a generic message when there is truly nothing recorded", () => {
    expect(friendlyBlockError(null)).toContain("Something went wrong");
    expect(friendlyBlockError(undefined)).toContain("Something went wrong");
    expect(friendlyBlockError("")).toContain("Something went wrong");
  });
});
