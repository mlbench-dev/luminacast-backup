import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import fs from "fs";
import path from "path";

const ACCENTS = [
  { code: "us", label: "American" },
  { code: "uk", label: "British" },
  { code: "au", label: "Australian" },
  { code: "in", label: "Indian" },
];

test.describe("Accent Verification", () => {
  test.setTimeout(10 * 60 * 1000); // 10 min

  test("creates 4 avatars via API with different accents and downloads voice MP3s", async ({
    page,
    request,
  }) => {
    await loginAsAdmin(page);

    // Get auth cookies from the page context
    const cookies = await page.context().cookies();
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");

    const resultsDir = path.join(process.cwd(), "test-results");
    if (!fs.existsSync(resultsDir)) fs.mkdirSync(resultsDir, { recursive: true });

    for (const accent of ACCENTS) {
      // Step 1: Create AI avatar via API
      const createResp = await request.post("/api/avatar/ai/create", {
        data: { name: `Accent Test ${accent.label}` },
        headers: { Cookie: cookieHeader },
      });
      expect(createResp.ok()).toBeTruthy();
      const { avatar_id } = await createResp.json();
      expect(avatar_id).toBeTruthy();

      // Step 2: Generate voice description
      const voiceDescResp = await request.post(
        "/api/avatar/ai/generate-voice-description",
        {
          data: { avatar_id },
          headers: { Cookie: cookieHeader },
        }
      );
      expect(voiceDescResp.ok()).toBeTruthy();
      const voiceDescData = await voiceDescResp.json();

      // Step 3: Generate voice previews WITH accent
      const previewResp = await request.post(
        "/api/avatar/ai/generate-voice-previews",
        {
          data: {
            avatar_id,
            voice_description: voiceDescData.voice_description,
            test_speech: `Hello, I am testing the ${accent.label} English accent for this avatar.`,
            gender: voiceDescData.suggested_filters?.gender || "female",
            language: "english",
            accent: accent.code,
          },
          headers: { Cookie: cookieHeader },
        }
      );
      expect(previewResp.ok()).toBeTruthy();
      const previewData = await previewResp.json();
      expect(previewData.previews.length).toBeGreaterThan(0);

      // Step 4: Approve the first voice
      const approveResp = await request.post("/api/avatar/ai/approve-voice", {
        data: {
          avatar_id,
          preview_id: previewData.previews[0].preview_id,
        },
        headers: { Cookie: cookieHeader },
      });
      expect(approveResp.ok()).toBeTruthy();

      // Step 5: Lock test script
      const lockResp = await request.post(
        `/api/avatar/ai/${avatar_id}/lock-test-script`,
        {
          data: {
            test_script: `Hello, I am speaking with a ${accent.label} English accent. This is a test of the accent pipeline.`,
          },
          headers: { Cookie: cookieHeader },
        }
      );
      expect(lockResp.ok()).toBeTruthy();

      // Step 6: Get locked voice audio
      const audioResp = await request.get(
        `/api/avatar/${avatar_id}/locked-voice-audio`,
        { headers: { Cookie: cookieHeader } }
      );
      expect(audioResp.ok()).toBeTruthy();
      const audioData = await audioResp.json();
      expect(audioData.audio_url).toBeTruthy();

      // Step 7: Download the MP3
      const mp3Resp = await request.get(audioData.audio_url);
      expect(mp3Resp.ok()).toBeTruthy();
      const audioBuffer = await mp3Resp.body();
      expect(audioBuffer.length).toBeGreaterThan(1000); // Basic non-empty check

      const outputPath = path.join(resultsDir, `accent-${accent.code}.mp3`);
      fs.writeFileSync(outputPath, audioBuffer);

      console.log(
        `[ACCENT TEST] ${accent.label}: saved to ${outputPath} (${audioBuffer.length} bytes)`
      );
    }

    // Verify all 4 files exist
    for (const accent of ACCENTS) {
      const filePath = path.join(resultsDir, `accent-${accent.code}.mp3`);
      expect(fs.existsSync(filePath)).toBe(true);
      const stat = fs.statSync(filePath);
      expect(stat.size).toBeGreaterThan(1000);
    }

    console.log(
      "\n[MANUAL VERIFICATION REQUIRED]\n" +
        "Listen to all 4 accent files in test-results/ and confirm they audibly differ:\n" +
        "  - accent-us.mp3 (American)\n" +
        "  - accent-uk.mp3 (British)\n" +
        "  - accent-au.mp3 (Australian)\n" +
        "  - accent-in.mp3 (Indian)\n"
    );
  });
});
