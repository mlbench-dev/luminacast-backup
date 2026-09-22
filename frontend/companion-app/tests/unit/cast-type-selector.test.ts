import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

/**
 * Source guard for the Stage-1 hero LIVE/Recorded toggle.
 *
 * The full SetupPhase is integration-heavy (react-query + API), so its runtime
 * behaviour is exercised by the Playwright spec
 * (tests/e2e/batch3-setup-cast-type-hero.spec.ts). This unit test pins the
 * pieces that are easy to regress silently: the accessible radio markup, the
 * LIVE duration lift, the mode-dependent copy, and the new POST fields.
 */

const SRC = path.resolve(__dirname, "../../src");
const setup = readFileSync(
  path.join(SRC, "components/cast-builder/SetupPhase.tsx"),
  "utf8",
);

describe("Stage 1 — hero cast-type toggle", () => {
  it("renders an accessible two-card radio group", () => {
    expect(setup).toContain('role="radiogroup"');
    expect(setup).toContain('role="radio"');
    expect(setup).toContain("aria-checked");
    expect(setup).toContain('data-testid="cast-type-selector"');
    // The two cards get per-option test-ids via a template literal
    // (cast-type-${opt.id}), rendering cast-type-recorded / cast-type-live.
    expect(setup).toContain("data-testid={`cast-type-${opt.id}`}");
    expect(setup).toContain('id: "recorded" as const');
    expect(setup).toContain('id: "live" as const');
  });

  it("uses large pictograms (Film + Radio with a pulsing dot)", () => {
    // 48-56px icons.
    expect(setup).toMatch(/w-12 h-12 sm:w-14 sm:h-14/);
    // LIVE broadcast indicator.
    expect(setup).toContain("bg-red-500");
    expect(setup).toContain("animate-pulse");
  });

  it("lifts the LIVE duration ceiling to 2 hours", () => {
    expect(setup).toContain(
      "DURATION_MARKS_SECONDS_LIVE = [60, 120, 300, 600, 900, 1800, 3600, 5400, 7200]",
    );
    expect(setup).toContain("LIVE_DEFAULT_DURATION = 1800");
    expect(setup).toContain("LIVE_MAX_SECONDS = 7200");
  });

  it("nudges LIVE setup: Smart Cast off + 30-min default", () => {
    // Switching into live turns Auto Cast off and seeds the live duration.
    expect(setup).toContain("setAutoCast(false)");
    expect(setup).toContain("lastLiveDuration");
    expect(setup).toContain("lastRecordedDuration");
  });

  it("shows mode-dependent helper copy", () => {
    expect(setup).toContain(
      "Polished short-form video. Best for hooks, ads, and TikTok-ready clips.",
    );
    expect(setup).toContain(
      "Long-form host-style content. Voiceover + your b-roll clips. Up to 2 hours.",
    );
  });

  it("renders the LIVE-only upload-clips card", () => {
    expect(setup).toContain('data-testid="live-upload-clips-card"');
    expect(setup).toContain("Upload your clips (optional)");
    expect(setup).toContain("UserVideoPickerDialog");
  });

  it("sends the new LIVE POST fields", () => {
    expect(setup).toContain("user_video_ids");
    expect(setup).toContain("live_mode_defaults");
    // Keys must match what _build_live_defaults_section (engine/cast_generator.py)
    // actually reads — voiceover / broll / max_duration_seconds. The previous
    // keys asserted here (primary_track/request_user_videos/b_roll_enabled)
    // didn't match anything the backend looked for, so live_mode_defaults was
    // silently ignored for every LIVE cast until this was fixed.
    expect(setup).toContain("voiceover: true");
    expect(setup).toContain("broll: true");
    expect(setup).toContain("max_duration_seconds");
  });

  it("contains no engine names in user-facing strings", () => {
    // Guard against leaking model/engine identifiers into the copy.
    for (const engine of ["InfiniteTalk", "MuseTalk", "Pexels"]) {
      // These may appear in code comments but must not be in rendered JSX text.
      const jsxText = setup
        .split("\n")
        .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
        .join("\n");
      expect(jsxText).not.toContain(`>${engine}`);
    }
  });
});
