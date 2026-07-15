import { describe, it, expect } from "vitest";
import {
  stripScriptMarkers,
  castToEditorStarterTimeline,
} from "../editorStarterMapping";
import type { Cast } from "../types";
import { CastStatus, BlockType, LayoutMode } from "../types";

/**
 * Regression-1: captions must never show internal script direction markers
 * like [sfx:record_scratch], (excited), [pause].
 */

describe("stripScriptMarkers", () => {
  it.each([
    ["[sfx:record_scratch] Okay wait.", "Okay wait."],
    ["Hold on [pause] now look.", "Hold on now look."],
    ["(excited) This is amazing!", "This is amazing!"],
    ["Honestly (whispering) you need this", "Honestly you need this"],
    ["[sfx:sparkle] (casual) Just $7 [pause] today (excited)!", "Just $7 today !"],
    ["Plain script with no markers.", "Plain script with no markers."],
    ["  leading and trailing  ", "leading and trailing"],
    ["", ""],
  ])("cleans %j → %j", (raw, expected) => {
    expect(stripScriptMarkers(raw)).toBe(expected);
  });

  it("collapses a marker-only string to empty", () => {
    expect(stripScriptMarkers("[sfx:boom]")).toBe("");
    expect(stripScriptMarkers("(excited)")).toBe("");
  });
});

describe("even-split caption fallback", () => {
  const MARKER_SCRIPT =
    "[sfx:record_scratch] Okay wait. (excited) This $7 cream [sfx:sparkle] " +
    "just changed everything. (whispering) You need this. [pause] (casual) Trust me.";

  const cast: Cast = {
    id: "cast_markers",
    status: CastStatus.TTS_READY,
    avatar_id: "avatar_1",
    max_duration_minutes: 5,
    loop: false,
    creation_paid: true,
    generation_progress: 100,
    created_at: "2026-06-15T00:00:00Z",
    output_format: "9:16",
    blocks: [
      {
        id: "b_001",
        type: BlockType.HOOK,
        position: 0,
        layout_mode: LayoutMode.FULL_AVATAR,
        auto_basket_enabled: false,
        auto_basket_timeout: 0,
        variant_count: 1,
        variants: [
          {
            id: "v_001",
            variant_label: "default",
            status: "ready",
            audio_key: "casts/cast_markers/audio/b_001.wav",
            tts_duration_seconds: 8.0,
            // No caption_words → forces the even-split fallback.
            script_text: MARKER_SCRIPT,
          },
        ],
      },
    ],
  };

  it("produces caption tokens with no leaked markers", () => {
    const { state } = castToEditorStarterTimeline(cast, {
      avatarFaceKey: "avatars/avatar_1/face.png",
      captionsEnabled: true,
    });

    const capAsset = state.assets["capasset_b_001"];
    expect(capAsset).toBeDefined();
    expect(capAsset.type).toBe("caption");

    const tokens = (capAsset as any).captions as Array<{ text: string }>;
    expect(tokens.length).toBeGreaterThan(0);

    const banned = ["[", "]", "(", ")", "sfx:", "pause", "excited", "whispering", "casual"];
    for (const t of tokens) {
      for (const bad of banned) {
        expect(t.text.toLowerCase()).not.toContain(bad);
      }
    }
  });
});
