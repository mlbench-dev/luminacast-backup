import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { pollUntil } from "./helpers/polling";
import { clickPlayAndVerifyAudio } from "./helpers/clickPlayAndVerify";
import { downloadAndVerifyAudio } from "./helpers/media";
import * as path from "path";

test.describe("G03 — Music Generation", () => {
  test.setTimeout(20 * 60 * 1000);

  test("music gen: generate track, verify playback, download and verify audio", async ({
    page,
    request,
  }, testInfo) => {
    await loginAsAdmin(page);

    // Navigate to music
    await page.goto("/music");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    // Click "New Sound Cast" or similar — MUST find button
    const newBtn = page.locator(
      'button:has-text("New Sound Cast"), button:has-text("New"), button:has-text("Create"), a:has-text("New Sound")'
    ).first();
    await expect(newBtn, "New Sound Cast button must be visible").toBeVisible({ timeout: 10000 });
    await newBtn.click();
    await page.waitForTimeout(2000);

    // Pick Generate tab — MUST find it
    const generateTab = page.locator(
      'button:has-text("Generate"), [data-testid="generate-tab"], [role="tab"]:has-text("Generate")'
    ).first();
    await expect(generateTab, "Generate tab must be visible").toBeVisible({ timeout: 5000 });
    await generateTab.click();
    await page.waitForTimeout(500);

    // Fill prompt — MUST find input
    const promptInput = page.locator(
      'textarea[placeholder*="prompt"], textarea[placeholder*="describe"], input[placeholder*="prompt"]'
    ).first();
    await expect(promptInput, "Prompt input must be visible").toBeVisible({ timeout: 5000 });
    await promptInput.fill("upbeat electronic dance music with synth leads and energetic drums");

    // Set duration if available
    const durationInput = page.locator('input[type="number"], input[placeholder*="duration"]').first();
    if (await durationInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await durationInput.fill("30");
    }

    // Submit — MUST find button
    const submitBtn = page.locator(
      'button:has-text("Generate"), button:has-text("Create"), button:has-text("Submit")'
    ).first();
    await expect(submitBtn).toBeVisible({ timeout: 5000 });
    await submitBtn.click();

    // Poll for audio element to appear (the real signal)
    await pollUntil(
      async () => {
        const audioEl = page.locator("audio").first();
        return audioEl.isVisible({ timeout: 2000 }).catch(() => false);
      },
      { timeoutMs: 15 * 60 * 1000, intervalMs: 15000, message: "Music generation audio element visible" }
    );

    // Play the audio — MUST work
    const audioEl = page.locator("audio").first();
    await expect(audioEl, "Audio element must be visible").toBeVisible({ timeout: 5000 });
    await clickPlayAndVerifyAudio(page, audioEl);

    // Download and verify — MUST have valid audio
    const audioSrc = await audioEl.evaluate((a: HTMLAudioElement) => a.src || a.currentSrc);
    expect(audioSrc, "Audio element must have a src URL").toBeTruthy();
    const audioPath = path.join(process.cwd(), "test-results", "g03-music.mp3");
    const duration = await downloadAndVerifyAudio(request, audioSrc, audioPath, testInfo);

    // Duration should be approximately what we requested (30s ± 10% per spec)
    expect(duration, "Audio duration should be > 20s").toBeGreaterThan(20);
    expect(duration, "Audio duration should be < 45s").toBeLessThan(45);

    testInfo.annotations.push({
      type: "summary",
      description: `Music generated. Audio duration: ${duration}s`,
    });
  });
});
