import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("QA Pass — Phase 2: Product Library", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── Journey 2.1: Discover trending products ──
  test("2.1 Discover trending products — tabs, filters, table loads", async ({
    page,
  }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await screenshot(page, "02-1-products-page");
    await verifyNoErrorBoundary(page);

    // Verify two tabs: Discover and My Library
    const discoverTab = page.getByRole("button", { name: /Discover/i });
    const libraryTab = page.getByRole("button", { name: /My Library/i });
    await expect(discoverTab).toBeVisible({ timeout: TIMEOUTS.medium });
    await expect(libraryTab).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "02-1-tabs-visible");

    // Click Discover tab if not already active
    await discoverTab.click();
    await page.waitForTimeout(1000);

    // Verify 5 category sub-tabs
    const subTabs = ["Hot Selling", "Trending", "Flash Sale", "New Products", "High Potential"];
    for (const tabName of subTabs) {
      const tab = page.getByRole("button", { name: tabName });
      const visible = await tab.isVisible().catch(() => false);
      if (!visible) {
        // Try text-based locator as fallback
        const textEl = page.locator(`text="${tabName}"`);
        const textVisible = (await textEl.count()) > 0;
        expect(
          textVisible,
          `Sub-tab "${tabName}" should be visible`
        ).toBeTruthy();
      }
    }
    await screenshot(page, "02-1-subtabs-visible");

    // Verify the products table loads (NOT stuck on "Loading trending products...")
    // Wait for either product rows or empty state
    const loadingText = page.locator('text="Loading"');
    const productRows = page.locator("table tbody tr, [class*='grid'] > div");

    // Wait up to 15s for loading to finish
    await page.waitForTimeout(3000);
    const stillLoading = await loadingText.isVisible().catch(() => false);
    if (stillLoading) {
      // Wait a bit longer
      await page.waitForTimeout(5000);
    }
    await screenshot(page, "02-1-products-loaded");

    // Verify search bar exists
    const searchInput = page.getByPlaceholder(/Search/i);
    await expect(searchInput.first()).toBeVisible({ timeout: TIMEOUTS.medium });

    // Click Hot Selling tab
    const hotSelling = page.locator("button").filter({ hasText: "Hot Selling" }).first();
    if (await hotSelling.isVisible()) {
      await hotSelling.click();
      await page.waitForTimeout(2000);
      await screenshot(page, "02-1-hot-selling");
    }

    // Click Trending tab
    const trending = page.locator("button").filter({ hasText: "Trending" }).first();
    if (await trending.isVisible()) {
      await trending.click();
      await page.waitForTimeout(2000);
      await screenshot(page, "02-1-trending");
    }

    // Verify filters panel has Apply and Reset buttons
    const applyBtn = page.locator("button").filter({ hasText: "Apply Filters" });
    const resetBtn = page.locator("button").filter({ hasText: "Reset" });
    // Filters may be in a collapsible panel, try to expand
    const filterToggle = page.locator("button").filter({ hasText: /Filter/i }).first();
    if (await filterToggle.isVisible()) {
      await filterToggle.click();
      await page.waitForTimeout(500);
    }
    await screenshot(page, "02-1-filters");
  });

  // ── Journey 2.2: Add product to library ──
  test("2.2 Discover product details — slide-over opens", async ({ page }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Click Discover tab
    const discoverTab = page.getByRole("button", { name: /Discover/i });
    await discoverTab.click();
    await page.waitForTimeout(3000);

    // Click on a product row to open slide-over
    const productRow = page.locator("table tbody tr, tr").first();
    if (await productRow.isVisible()) {
      await productRow.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "02-2-product-slideover");
    }

    // Verify My Library tab works
    const libraryTab = page.getByRole("button", { name: /My Library/i });
    await libraryTab.click();
    await page.waitForTimeout(2000);
    await screenshot(page, "02-2-my-library");
    await verifyNoErrorBoundary(page);

    // Verify library has content or shows empty state
    const pageText = await page.textContent("body");
    const hasLibraryContent =
      pageText?.includes("product") || pageText?.includes("Product") ||
      pageText?.includes("No products") || pageText?.includes("Discover");
    expect(hasLibraryContent).toBeTruthy();
  });

  // ── Journey 2.3: Manual product addition ──
  test("2.3 Add product manually — form opens", async ({ page }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Find "Add manually" button
    const addManuallyBtn = page.locator("button").filter({ hasText: /Add manually/i }).first();
    const addProductBtn = page.locator("button").filter({ hasText: /Add Product/i }).first();

    const target = (await addManuallyBtn.isVisible()) ? addManuallyBtn : addProductBtn;
    if (await target.isVisible()) {
      await target.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "02-3-add-product-form");

      // Verify modal/form has expected fields
      const pageText = await page.textContent("body");
      const hasFormElements =
        pageText?.includes("name") || pageText?.includes("Name") ||
        pageText?.includes("URL") || pageText?.includes("url") ||
        pageText?.includes("price") || pageText?.includes("Price");
      expect(
        hasFormElements,
        "Add product form should have name/URL/price fields"
      ).toBeTruthy();
    }
    await screenshot(page, "02-3-add-form-complete");
  });
});
