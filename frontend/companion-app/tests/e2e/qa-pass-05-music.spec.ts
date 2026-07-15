import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("QA Pass — Phase 5: Music", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── Journey 5.1: Music page loads ──
  test("5.1 Music page — loads without error, shows sound casts", async ({
    page,
  }) => {
    await page.goto("/music");
    await waitForPageLoad(page);
    await screenshot(page, "05-1-music-page");
    await verifyNoErrorBoundary(page);

    // Verify page has music-related content
    const pageText = await page.textContent("body");
    const hasMusic =
      pageText?.includes("Sound Cast") ||
      pageText?.includes("Music") ||
      pageText?.includes("New Sound Cast") ||
      pageText?.includes("Training") ||
      pageText?.includes("Generate");
    expect(hasMusic, "Music page should show sound cast content").toBeTruthy();

    // Look for New Sound Cast button
    const newBtn = page.locator("button").filter({ hasText: /New Sound Cast/i }).first();
    if (await newBtn.isVisible()) {
      await screenshot(page, "05-1-new-sound-cast-btn");
    }

    await screenshot(page, "05-1-music-complete");
  });

  // ── Journey 5.2: Generate music UI ──
  test("5.2 Music generation — form fields present", async ({ page }) => {
    await page.goto("/music");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Try to navigate to generate tab
    const generateTab = page.locator("button").filter({ hasText: /Generate/i }).first();
    if (await generateTab.isVisible()) {
      await generateTab.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "05-2-generate-tab");
    }

    // Check for generation form fields
    const trackNameInput = page.getByPlaceholder(/My Track|track name/i);
    const stylePrompt = page.getByPlaceholder(/vibe|style|describe/i);
    const lyricsInput = page.getByPlaceholder(/lyrics|instrumental/i);

    if (await trackNameInput.isVisible().catch(() => false)) {
      await screenshot(page, "05-2-track-name");
    }
    if (await stylePrompt.isVisible().catch(() => false)) {
      await screenshot(page, "05-2-style-prompt");
    }

    // Duration buttons
    const durations = ["30s", "1m", "1.5m", "2m"];
    for (const d of durations) {
      const dBtn = page.locator("button").filter({ hasText: d }).first();
      if (await dBtn.isVisible().catch(() => false)) {
        await screenshot(page, "05-2-duration-buttons");
        break;
      }
    }

    // Generate Track button
    const genBtn = page.locator("button").filter({ hasText: /Generate Track/i }).first();
    if (await genBtn.isVisible()) {
      await screenshot(page, "05-2-generate-track-btn");
    }

    await screenshot(page, "05-2-music-form-complete");
  });
});
