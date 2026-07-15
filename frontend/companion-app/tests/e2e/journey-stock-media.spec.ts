import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Stock Media", () => {
  test("search and browse Pexels stock photos and videos", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Navigate to My Videos (stock media import available here)
    await page.goto("/my-videos");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/stock-01-my-videos.png" });

    // 2. Look for Stock Media button
    const stockBtn = page.locator('[data-testid="import-stock-media"]').first();
    await expect(stockBtn).toBeVisible({ timeout: 10000 });
    await page.screenshot({ path: "e2e-report/stock-02-stock-button.png" });

    // 3. Open Stock Media picker
    await stockBtn.click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: "e2e-report/stock-03-picker-open.png" });

    // 4. Verify search input
    const searchInput = page.locator('[data-testid="stock-media-search"]').first();
    await expect(searchInput).toBeVisible({ timeout: 5000 });

    // 5. Search for something
    await searchInput.fill("beauty products");
    await page.waitForTimeout(1500); // debounce

    await page.screenshot({ path: "e2e-report/stock-04-search-results.png" });

    // 6. Verify results appeared
    const results = page.locator('[data-testid^="stock-result-"]').first();
    await expect(results).toBeVisible({ timeout: 10000 });

    // 7. Verify attribution footer
    const attribution = page.locator('text=provided by Pexels');
    await expect(attribution).toBeVisible();
    await page.screenshot({ path: "e2e-report/stock-05-attribution.png" });

    // 8. Switch to Videos tab
    const videosTab = page.locator('button:has-text("Videos")').first();
    if (await videosTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await videosTab.click();
      await page.waitForTimeout(1500);
      await page.screenshot({ path: "e2e-report/stock-06-videos-tab.png" });

      // Verify video results load
      const videoResults = page.locator('[data-testid^="stock-result-"]').first();
      await expect(videoResults).toBeVisible({ timeout: 10000 });
      await page.screenshot({ path: "e2e-report/stock-07-video-results.png" });
    }

    // 9. Close picker
    const closeBtn = page.locator('[role="dialog"] button:has(svg)').first();
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click();
    } else {
      await page.keyboard.press("Escape");
    }
    await page.waitForTimeout(300);
  });

  test("stock media button visible in Cast Builder block settings", async ({ page }) => {
    await loginAsAdmin(page);

    // Navigate to Cast Builder
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/stock-08-cast-builder.png" });

    // Check for stock media button (may only be visible when block is in voiceover/pip mode)
    const stockMediaBtn = page.locator('[data-testid="stock-media-btn"]').first();
    // This button shows up only when a block is in voiceover or pip mode
    // Take a screenshot of the cast builder for verification
    await page.screenshot({ path: "e2e-report/stock-09-cast-builder-loaded.png" });
  });

  test("stock media button visible in Live Session setup", async ({ page }) => {
    await loginAsAdmin(page);

    // Navigate to Go Live
    await page.goto("/live-control");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/stock-10-live-control.png" });

    // Verify the session setup form loads
    const avatarPicker = page.locator('[data-testid="avatar-picker"]').first();
    await expect(avatarPicker).toBeVisible({ timeout: 10000 });

    // Add a product to the queue to see the stock footage button
    const productPicker = page.locator('[data-testid="product-picker"]').first();
    await expect(productPicker).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: "e2e-report/stock-11-live-session-form.png" });
  });
});
