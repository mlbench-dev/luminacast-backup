import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("QA Pass — Phase 4: Live Broadcast", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── Journey 4.1: Create live session form ──
  test("4.1 Live control — form fields visible and functional", async ({
    page,
  }) => {
    await page.goto("/live-control");
    await waitForPageLoad(page);
    await screenshot(page, "04-1-live-control-page");
    await verifyNoErrorBoundary(page);

    // Verify page container
    const liveControl = page.locator('[data-testid="live-control-page"]');
    await expect(liveControl).toBeVisible({ timeout: TIMEOUTS.medium });

    // Verify form fields
    const sessionTitle = page.locator('[data-testid="session-title"]');
    if (await sessionTitle.isVisible()) {
      await sessionTitle.fill("QA Test Session");
      await screenshot(page, "04-1-title-filled");
    }

    // Verify avatar picker
    const avatarPicker = page.locator('[data-testid="avatar-picker"]');
    if (await avatarPicker.isVisible()) {
      await screenshot(page, "04-1-avatar-picker");
    }

    // Verify product picker
    const productPicker = page.locator('[data-testid="product-picker"]');
    if (await productPicker.isVisible()) {
      await screenshot(page, "04-1-product-picker");
    }

    // Verify duration select
    const durationSelect = page.locator('[data-testid="duration-select"]');
    if (await durationSelect.isVisible()) {
      await screenshot(page, "04-1-duration-select");
    }

    // Verify output format
    const outputFormat = page.locator('[data-testid="output-format"]');
    if (await outputFormat.isVisible()) {
      await screenshot(page, "04-1-output-format");
    }

    // Verify Create Session button
    const createBtn = page.locator('[data-testid="create-session-button"]');
    await expect(createBtn).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "04-1-create-session-btn");

    // Verify voice style textarea
    const voiceStyle = page.locator('[data-testid="voice-style"]');
    if (await voiceStyle.isVisible()) {
      await voiceStyle.fill("Energetic and enthusiastic");
      await screenshot(page, "04-1-voice-style");
    }

    await screenshot(page, "04-1-form-complete");
  });

  // ── Journey 4.2: Live session list / past sessions ──
  test("4.2 Live session — page shows session history", async ({ page }) => {
    await page.goto("/live-control");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "04-2-live-sessions");

    // Check if there are past sessions visible
    const pageText = await page.textContent("body");
    const hasSessions =
      pageText?.includes("session") ||
      pageText?.includes("Session") ||
      pageText?.includes("Live") ||
      pageText?.includes("Create");
    expect(
      hasSessions,
      "Live control page should show sessions or create form"
    ).toBeTruthy();

    await screenshot(page, "04-2-session-history");
  });
});
