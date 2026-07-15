import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

/**
 * Hero LIVE/Recorded toggle for Stage 1.
 *
 * Covers the upgraded cast-type picker: two large role="radio" cards, the
 * LIVE duration lift (ticks beyond 720s), the LIVE-only upload-clips card,
 * and the mode-dependent helper copy.
 */
test.describe("Batch 3 — Stage 1 hero cast-type toggle", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
  });

  test("renders both cards as role=radio", async ({ page }) => {
    const recorded = page.getByTestId("cast-type-recorded");
    const live = page.getByTestId("cast-type-live");

    await expect(recorded).toBeVisible({ timeout: TIMEOUTS.medium });
    await expect(live).toBeVisible();
    await expect(recorded).toHaveAttribute("role", "radio");
    await expect(live).toHaveAttribute("role", "radio");

    // Defaults to recorded.
    await expect(recorded).toHaveAttribute("aria-checked", "true");
    await expect(live).toHaveAttribute("aria-checked", "false");

    await screenshot(page, "casttype-hero-01-both-cards");
  });

  test("LIVE lifts duration ceiling and shows upload + copy", async ({ page }) => {
    await page.getByTestId("cast-type-live").click();
    await page.waitForTimeout(300);

    // aria-checked moves to LIVE.
    await expect(page.getByTestId("cast-type-live")).toHaveAttribute(
      "aria-checked",
      "true",
    );

    // Helper copy switches to the long-form message.
    await expect(page.getByTestId("cast-type-helper")).toContainText(
      "Up to 2 hours",
    );

    // Upload-clips card appears only in LIVE mode.
    await expect(page.getByTestId("live-upload-clips-card")).toBeVisible();

    // Reveal the duration slider (manual mode) and assert the lifted ticks.
    await page.getByText("set manually", { exact: false }).first().click();
    await page.waitForTimeout(200);
    const marks = page.getByTestId("duration-marks");
    await expect(marks).toBeVisible();
    // 7200s == 120m — the 2-hour ceiling tick must be present.
    await expect(marks).toContainText(/120m|7200s/);

    await screenshot(page, "casttype-hero-02-live-defaults");
  });

  test("Recorded hides the upload-clips card", async ({ page }) => {
    // Switch to LIVE then back to confirm the card disappears.
    await page.getByTestId("cast-type-live").click();
    await page.waitForTimeout(200);
    await expect(page.getByTestId("live-upload-clips-card")).toBeVisible();

    await page.getByTestId("cast-type-recorded").click();
    await page.waitForTimeout(200);
    await expect(page.getByTestId("live-upload-clips-card")).toHaveCount(0);
    await expect(page.getByTestId("cast-type-helper")).toContainText(
      "TikTok-ready",
    );

    await screenshot(page, "casttype-hero-03-recorded-no-upload");
  });
});
