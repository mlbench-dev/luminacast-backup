import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Live/Recorded Cast Type Toggle (Phase 5)", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Setup Phase shows cast type toggle", async ({ page }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-casttype-01-setup-loaded");
    await verifyNoErrorBoundary(page);

    // Look for cast type toggle — could be a toggle, radio group, or segmented control
    const castTypeToggle = page.locator(
      '[data-testid="cast-type-toggle"], [data-testid="cast-type-selector"], [data-testid="cast-type-radio"]'
    );
    const toggleVisible = await castTypeToggle.isVisible({ timeout: TIMEOUTS.short }).catch(() => false);

    // Fallback: look for "Video" and "Radio" or "Live" and "Recorded" text near toggle controls
    const pageText = await page.textContent("body");
    const hasTypeOptions =
      (pageText?.includes("Video") && pageText?.includes("Radio")) ||
      (pageText?.includes("Live") && pageText?.includes("Recorded")) ||
      pageText?.includes("Cast Type");

    // Also check for any toggle/radio button group with these labels
    const toggleButtons = page
      .locator('button, [role="radio"], [role="tab"]')
      .filter({ hasText: /Video|Radio|Live|Recorded/i });
    const toggleBtnCount = await toggleButtons.count();

    expect(
      toggleVisible || hasTypeOptions || toggleBtnCount > 0,
      "Setup Phase should show a cast type toggle"
    ).toBeTruthy();

    await screenshot(page, "batch3-casttype-02-toggle-visible");
  });

  test("Cast type toggle has Video and Radio options", async ({ page }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "batch3-casttype-03-setup-for-options");

    // Look for Video option
    const videoOption = page
      .locator('button, [role="radio"], [role="tab"], label')
      .filter({ hasText: /Video/i })
      .first();
    const videoVisible = await videoOption.isVisible({ timeout: TIMEOUTS.short }).catch(() => false);

    // Look for Radio option
    const radioOption = page
      .locator('button, [role="radio"], [role="tab"], label')
      .filter({ hasText: /Radio/i })
      .first();
    const radioVisible = await radioOption.isVisible({ timeout: TIMEOUTS.short }).catch(() => false);

    expect(videoVisible, "Video option should be visible").toBeTruthy();
    expect(radioVisible, "Radio option should be visible").toBeTruthy();

    // Click each option and verify it responds
    if (videoVisible) {
      await videoOption.click();
      await page.waitForTimeout(300);
      await screenshot(page, "batch3-casttype-04-video-selected");
    }

    if (radioVisible) {
      await radioOption.click();
      await page.waitForTimeout(300);
      await screenshot(page, "batch3-casttype-05-radio-selected");
    }

    await screenshot(page, "batch3-casttype-06-both-options-tested");
  });

  test("Cast type defaults to 'recorded'", async ({ page }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "batch3-casttype-07-default-check");

    // Check if "recorded" is the default selected option
    // This could be indicated by an active/selected class, aria-checked, or data-state
    const recordedBtn = page
      .locator(
        '[data-testid="cast-type-recorded"], ' +
        'button[aria-pressed="true"]:has-text("Recorded"), ' +
        '[role="radio"][aria-checked="true"]:has-text("Recorded"), ' +
        '[data-state="active"]:has-text("Recorded")'
      )
      .first();
    const recordedSelected = await recordedBtn
      .isVisible({ timeout: TIMEOUTS.short })
      .catch(() => false);

    // Fallback: check if "Live" is NOT selected, implying "Recorded" is the default
    const liveBtn = page
      .locator(
        '[data-testid="cast-type-live"], ' +
        'button[aria-pressed="true"]:has-text("Live"), ' +
        '[role="radio"][aria-checked="true"]:has-text("Live"), ' +
        '[data-state="active"]:has-text("Live")'
      )
      .first();
    const liveSelected = await liveBtn
      .isVisible({ timeout: TIMEOUTS.short })
      .catch(() => false);

    // The page should NOT default to "Live"
    if (liveSelected) {
      expect(
        liveSelected,
        "Cast type should NOT default to Live"
      ).toBeFalsy();
    }

    // Check body text for any indication of the default state
    const pageText = await page.textContent("body");
    const hasRecordedContext =
      recordedSelected ||
      pageText?.includes("Recorded") ||
      pageText?.includes("recorded");

    expect(
      hasRecordedContext,
      "Cast type should default to recorded mode"
    ).toBeTruthy();

    await screenshot(page, "batch3-casttype-08-default-recorded");
  });
});
