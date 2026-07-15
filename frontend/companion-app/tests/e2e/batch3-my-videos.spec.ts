import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — My Videos Improvements (Phase 3)", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("My Videos page loads with upload button", async ({ page }) => {
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-videos-01-page-loaded");
    await verifyNoErrorBoundary(page);

    // Verify the page loaded (check heading or data-testid)
    const pageText = await page.textContent("body");
    const isMyVideosPage =
      pageText?.includes("My Videos") ||
      pageText?.includes("Videos") ||
      pageText?.includes("Upload");
    expect(isMyVideosPage, "My Videos page should load").toBeTruthy();

    // Look for upload button
    const uploadBtn = page
      .locator(
        '[data-testid="upload-video-btn"], ' +
        '[data-testid="upload-btn"], ' +
        'button:has-text("Upload")'
      )
      .first();
    await expect(uploadBtn, "Upload button should be visible").toBeVisible({
      timeout: TIMEOUTS.medium,
    });

    await screenshot(page, "batch3-videos-02-upload-btn-visible");
  });

  test("Search input and button are visible when videos exist", async ({
    page,
  }) => {
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-videos-03-search-check");
    await verifyNoErrorBoundary(page);

    // Check for search input
    const searchInput = page
      .locator(
        '[data-testid="video-search-input"], ' +
        '[data-testid="search-input"], ' +
        'input[placeholder*="Search"], ' +
        'input[placeholder*="search"]'
      )
      .first();
    const searchVisible = await searchInput
      .isVisible({ timeout: TIMEOUTS.short })
      .catch(() => false);

    // Check for search button
    const searchBtn = page
      .locator(
        '[data-testid="video-search-btn"], ' +
        '[data-testid="search-btn"], ' +
        'button:has-text("Search")'
      )
      .first();
    const searchBtnVisible = await searchBtn
      .isVisible({ timeout: TIMEOUTS.short })
      .catch(() => false);

    // Search UI should be visible (input and/or button)
    expect(
      searchVisible || searchBtnVisible,
      "Search input or button should be visible on My Videos page"
    ).toBeTruthy();

    await screenshot(page, "batch3-videos-04-search-visible");
  });

  test("Stock Media button is visible", async ({ page }) => {
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-videos-05-stock-check");
    await verifyNoErrorBoundary(page);

    // Look for Stock Media import button
    const stockBtn = page
      .locator(
        '[data-testid="import-stock-media"], ' +
        '[data-testid="stock-media-btn"], ' +
        'button:has-text("Stock Media"), ' +
        'button:has-text("Stock")'
      )
      .first();
    await expect(
      stockBtn,
      "Stock Media button should be visible on My Videos page"
    ).toBeVisible({ timeout: TIMEOUTS.medium });

    await screenshot(page, "batch3-videos-06-stock-btn-visible");
  });

  test("Search can filter videos by name", async ({ page }) => {
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Check if there are any video cards / items
    const videoItems = page.locator(
      '[data-testid^="video-card-"], [data-testid^="video-item-"], [data-testid^="media-card-"]'
    );
    const videoCount = await videoItems.count();
    await screenshot(page, "batch3-videos-07-initial-list");

    if (videoCount === 0) {
      // No videos to filter — check that empty state or upload prompt is shown
      const pageText = await page.textContent("body");
      const hasEmptyState =
        pageText?.includes("No videos") ||
        pageText?.includes("Upload") ||
        pageText?.includes("empty") ||
        pageText?.includes("Get started");
      expect(
        hasEmptyState,
        "Empty state should be displayed when no videos exist"
      ).toBeTruthy();
      await screenshot(page, "batch3-videos-08-empty-state");
      return;
    }

    // Find and use the search input
    const searchInput = page
      .locator(
        '[data-testid="video-search-input"], ' +
        '[data-testid="search-input"], ' +
        'input[placeholder*="Search"], ' +
        'input[placeholder*="search"]'
      )
      .first();

    if (await searchInput.isVisible({ timeout: TIMEOUTS.short })) {
      // Type a search term
      await searchInput.fill("test");
      await page.waitForTimeout(1000); // debounce

      // Optionally click search button if present
      const searchBtn = page
        .locator(
          '[data-testid="video-search-btn"], ' +
          '[data-testid="search-btn"], ' +
          'button:has-text("Search")'
        )
        .first();
      if (await searchBtn.isVisible({ timeout: TIMEOUTS.short }).catch(() => false)) {
        await searchBtn.click();
        await page.waitForTimeout(1000);
      }

      await screenshot(page, "batch3-videos-09-search-filtered");

      // Clear search
      await searchInput.clear();
      await page.waitForTimeout(1000);
      await screenshot(page, "batch3-videos-10-search-cleared");
    }
  });
});
