import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test.describe("Journey: Voice Corpus + Rewrite in My Voice", () => {
  test("upload voice example and verify processing", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Navigate to avatar edit page
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle");
    const editLink = page.locator("text=Edit profile").first();
    if (!(await editLink.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip(true, "No avatars available for editing");
      return;
    }
    await editLink.click();
    await page.waitForURL(/\/my-avatar\/.*\/edit/);

    // 2. Click the Voice Examples tab
    const voiceTab = page.locator("button:has-text(\"Voice Examples\")");
    await expect(voiceTab).toBeVisible({ timeout: 5000 });
    await voiceTab.click();
    await page.screenshot({ path: "e2e-report/voice-01-voice-tab.png" });

    // 3. Verify the voice corpus tab rendered
    const corpusTab = page.locator("[data-testid=\"voice-corpus-tab\"]");
    await expect(corpusTab).toBeVisible({ timeout: 5000 });

    // 4. Upload a test video file
    const testVideoPath = path.join(__dirname, "fixtures", "test-voice-sample.mp4");
    const fileInput = page.locator("[data-testid=\"voice-file-input\"]");
    await fileInput.setInputFiles(testVideoPath);
    await page.screenshot({ path: "e2e-report/voice-02-uploading.png" });

    // 5. Wait for entry to appear (pending or processing)
    const entryLocator = page.locator("[data-testid^=\"corpus-entry-\"]").first();
    await expect(entryLocator).toBeVisible({ timeout: 30000 });
    await page.screenshot({ path: "e2e-report/voice-03-entry-appeared.png" });

    // 6. Wait for ready status (up to 5 min for Whisper + Pyannote)
    const readyBadge = entryLocator.locator("text=ready");
    await expect(readyBadge).toBeVisible({ timeout: 300000 });
    await page.screenshot({ path: "e2e-report/voice-04-ready.png" });

    // 7. Verify transcript preview is visible
    const transcript = page.locator("[data-testid^=\"transcript-\"]").first();
    await expect(transcript).toBeVisible();

    // 8. Verify audio player
    const audio = page.locator("audio").first();
    await expect(audio).toBeVisible();
    await page.screenshot({ path: "e2e-report/voice-05-with-audio.png" });
  });
});
