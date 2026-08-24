import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

/**
 * Guard for Step 6: the avatar "Background" feature is labelled "Scene" in the
 * UI. Code identifiers (background_id, AvatarBackground, look_type:"background",
 * query keys, API paths, CSS) are intentionally left untouched, so this test
 * targets the specific user-visible label strings that were renamed rather than
 * grepping for the bare word "background".
 */

const SRC = path.resolve(__dirname, "../../src");

const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8");

// These guards target user-visible label strings only. Code comments may
// still reference an old placeholder name (e.g. documenting what a fallback
// replaced) without being rendered, so strip comment lines before checking.
const stripComments = (source: string): string =>
  source
    .split("\n")
    .filter((l) => {
      const t = l.trim();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*");
    })
    .join("\n");

// Old user-facing label literals that must no longer appear, keyed by the file
// that owned them. Each is a string a user could read in the UI.
const RETIRED_LABELS: Record<string, string[]> = {
  // components/avatar/AvatarBackgrounds.tsx was removed entirely (the
  // AvatarBackground/name-matching scene system it managed was retired —
  // see engine.cast_generator.stamp_template_defaults_on_blocks's
  // docstring) — its retired labels can't reappear in a file that no
  // longer exists, so there's nothing left to guard here.
  "components/cast-builder/BackgroundPicker.tsx": [
    "Keep avatar's background",
    "Blur original background",
    'alt="Background"',
  ],
  "components/cast-builder/EffectsPanel.tsx": [">Background<", "Replace avatar background"],
  "components/cast-builder/ScriptBlockEditor.tsx": ["Default background"],
  "components/cast-builder/scriptphase/BlockVisualPreview.tsx": [
    "Choose background",
  ],
  "components/cast-builder/scriptphase/AvatarLookPicker.tsx": [
    "Custom background",
    "Background generation started",
    "Generate a new background",
    "Describe the background",
    "only the background changes",
  ],
  "components/avatar/AddLookDialog.tsx": ["You can change background"],
  "pages/avatar/EditAvatarPage.tsx": [
    'label: "Backgrounds"',
    "No backgrounds yet",
  ],
  "pages/avatar/AIAvatarSetup.tsx": ["generate background photos"],
};

describe("Step 6 — Background → Scene UI labels", () => {
  for (const [file, labels] of Object.entries(RETIRED_LABELS)) {
    it(`${file} shows no user-facing "Background" label`, () => {
      const source = stripComments(read(file));
      for (const label of labels) {
        expect(
          source.includes(label),
          `"${label}" should have been renamed to its "Scene" equivalent in ${file}`,
        ).toBe(false);
      }
    });
  }

  it("keeps code identifiers and API paths intact", () => {
    const picker = read("components/cast-builder/BackgroundPicker.tsx");
    expect(picker).toContain("BackgroundConfig");
    expect(picker).toContain("image_key");

    const editor = read("components/cast-builder/ScriptBlockEditor.tsx");
    expect(editor).toContain("background_id");
    expect(editor).toContain('queryKey: ["avatar-backgrounds"');

    const looks = read(
      "components/cast-builder/scriptphase/AvatarLookPicker.tsx",
    );
    expect(looks).toContain("background_prompt");
    expect(looks).toContain('look_type: "background"');
  });
});
