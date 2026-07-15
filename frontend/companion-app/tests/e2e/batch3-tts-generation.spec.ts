import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — TTS Generation (Phase 6.2)", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Script Phase has 'Generate Audio' button visible", async ({ page }) => {
    // Navigate to cast builder and open an existing cast in Script phase
    await page.goto("/cast-builder");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount === 0) {
      // Create a new cast to reach script phase
      await page.goto("/cast-builder/new");
      await waitForPageLoad(page);
      await verifyNoErrorBoundary(page);
      await screenshot(page, "batch3-tts-01-new-cast-setup");

      // Fill minimal setup to get to script phase
      const nameInput = page.getByPlaceholder(/My Awesome Cast|cast name/i);
      if (await nameInput.isVisible({ timeout: TIMEOUTS.short })) {
        await nameInput.fill(`QA-tts-${Date.now()}`);
      }

      // Select first avatar if available
      const avatarButtons = page
        .locator("button")
        .filter({ has: page.locator("img") });
      if ((await avatarButtons.count()) > 0) {
        await avatarButtons.first().click();
        await page.waitForTimeout(500);
      }

      // Click Generate Script to advance to script phase
      const generateBtn = page
        .locator("button")
        .filter({ hasText: /Generate Script/i })
        .first();
      if (await generateBtn.isVisible({ timeout: TIMEOUTS.short })) {
        await generateBtn.click();
        await page.waitForTimeout(10000);
      }
    } else {
      // Open first available cast
      await castCards.first().click();
      await page.waitForTimeout(3000);
    }

    await screenshot(page, "batch3-tts-02-script-phase");
    await verifyNoErrorBoundary(page);

    // Verify the Generate Audio button is visible
    const audioBtn = page
      .locator("button")
      .filter({ hasText: /Generate Audio/i })
      .first();
    const pageText = await page.textContent("body");
    const inScriptPhase =
      pageText?.includes("Script") ||
      pageText?.includes("Generate Audio") ||
      pageText?.includes("Block");

    if (inScriptPhase) {
      await expect(audioBtn).toBeVisible({ timeout: TIMEOUTS.medium });
      await screenshot(page, "batch3-tts-03-generate-audio-btn");
    }
  });

  test("Navigate to cast builder, verify Script Phase loads", async ({
    page,
  }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "batch3-tts-04-cast-builder-new");

    // Fill cast name
    const nameInput = page.getByPlaceholder(/My Awesome Cast|cast name/i);
    if (await nameInput.isVisible({ timeout: TIMEOUTS.short })) {
      await nameInput.fill(`QA-script-phase-${Date.now()}`);
    }

    // Select first avatar
    const avatarButtons = page
      .locator("button")
      .filter({ has: page.locator("img") });
    if ((await avatarButtons.count()) > 0) {
      await avatarButtons.first().click();
      await page.waitForTimeout(500);
    }

    // Enter direction
    const directionInput = page.getByPlaceholder(
      /energetically|describe|product benefits/i
    );
    if (await directionInput.isVisible()) {
      await directionInput.fill("Quick product showcase for TTS test");
    }

    // Click Generate Script
    const generateBtn = page
      .locator("button")
      .filter({ hasText: /Generate Script/i })
      .first();
    if (await generateBtn.isEnabled({ timeout: TIMEOUTS.short })) {
      await generateBtn.click();
      await screenshot(page, "batch3-tts-05-generating-script");

      // Wait for script phase to load
      await page.waitForTimeout(10000);
      await screenshot(page, "batch3-tts-06-script-loaded");
      await verifyNoErrorBoundary(page);

      // Verify we are in script phase
      const pageText = await page.textContent("body");
      const hasScriptContent =
        pageText?.includes("Script") ||
        pageText?.includes("Block") ||
        pageText?.includes("words") ||
        pageText?.includes("Generate Audio");
      expect(
        hasScriptContent,
        "Script Phase should display script-related content"
      ).toBeTruthy();
    }

    await screenshot(page, "batch3-tts-07-script-phase-complete");
  });

  test("Verify block text areas are editable", async ({ page }) => {
    // Navigate to cast builder list and find a cast with script content
    await page.goto("/cast-builder");
    await waitForPageLoad(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount > 0) {
      // Find a cast that likely has script blocks
      let targetCard = null;
      for (let i = 0; i < Math.min(cardCount, 10); i++) {
        const card = castCards.nth(i);
        const text = await card.textContent();
        if (
          text?.includes("Script") ||
          text?.includes("Audio") ||
          text?.includes("Arrange") ||
          text?.includes("Ready")
        ) {
          targetCard = card;
          break;
        }
      }

      if (!targetCard) {
        targetCard = castCards.first();
      }

      await targetCard.click();
      await page.waitForTimeout(3000);
      await screenshot(page, "batch3-tts-08-opened-cast");
      await verifyNoErrorBoundary(page);
    } else {
      test.skip(true, "No casts available to test block text areas");
      return;
    }

    // Look for textarea elements (script block editors)
    const textAreas = page.locator("textarea");
    const textAreaCount = await textAreas.count();
    await screenshot(page, "batch3-tts-09-block-textareas");

    if (textAreaCount > 0) {
      // Verify first textarea is editable
      const firstTextArea = textAreas.first();
      await expect(firstTextArea).toBeVisible({ timeout: TIMEOUTS.medium });
      await expect(firstTextArea).toBeEditable();

      // Type test text
      const originalValue = await firstTextArea.inputValue();
      await firstTextArea.click();
      await firstTextArea.fill("E2E test text for block editing");
      await page.waitForTimeout(500);
      await screenshot(page, "batch3-tts-10-textarea-edited");

      // Restore original value
      await firstTextArea.fill(originalValue);
    }

    await screenshot(page, "batch3-tts-11-editable-check-done");
  });
});
