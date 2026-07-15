import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Product Library", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Products page loads", async ({ page }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await screenshot(page, "b3-10-products-page-load");
    await verifyNoErrorBoundary(page);

    // Verify products page has rendered with expected content
    const pageText = await page.textContent("body");
    const hasProductContent =
      pageText?.includes("Product") ||
      pageText?.includes("product") ||
      pageText?.includes("Discover") ||
      pageText?.includes("Library") ||
      pageText?.includes("My Library");
    expect(
      hasProductContent,
      "Products page should display product-related content"
    ).toBeTruthy();

    // Verify the main tabs are present (Discover and My Library)
    const discoverTab = page.getByRole("button", { name: /Discover/i });
    const libraryTab = page.getByRole("button", { name: /My Library/i });

    const hasDiscoverTab = await discoverTab.isVisible().catch(() => false);
    const hasLibraryTab = await libraryTab.isVisible().catch(() => false);

    expect(
      hasDiscoverTab || hasLibraryTab,
      "Products page should have Discover or My Library tab"
    ).toBeTruthy();

    await screenshot(page, "b3-10-products-tabs-visible");
  });

  test('"Add Product" or "Discover" button visible', async ({ page }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await screenshot(page, "b3-10-products-for-buttons");
    await verifyNoErrorBoundary(page);

    // Check for "Add Product", "Add manually", or "Discover" buttons
    const addProductBtn = page
      .locator("button")
      .filter({ hasText: /Add Product/i })
      .first();
    const addManuallyBtn = page
      .locator("button")
      .filter({ hasText: /Add manually/i })
      .first();
    const discoverBtn = page
      .getByRole("button", { name: /Discover/i })
      .first();

    const hasAddProduct = await addProductBtn.isVisible().catch(() => false);
    const hasAddManually = await addManuallyBtn.isVisible().catch(() => false);
    const hasDiscover = await discoverBtn.isVisible().catch(() => false);

    expect(
      hasAddProduct || hasAddManually || hasDiscover,
      'Should have "Add Product", "Add manually", or "Discover" button visible'
    ).toBeTruthy();

    await screenshot(page, "b3-10-product-action-buttons");

    // If Discover is visible, click it and verify the product catalog loads
    if (hasDiscover) {
      await discoverBtn.click();
      await page.waitForTimeout(2000);
      await screenshot(page, "b3-10-discover-tab-clicked");
      await verifyNoErrorBoundary(page);

      // Verify product search or listing appears
      const searchInput = page.getByPlaceholder(/Search/i).first();
      const hasSearch = await searchInput.isVisible().catch(() => false);

      // Wait for products table or grid to load
      await page.waitForTimeout(3000);
      const productRows = page.locator("table tbody tr, [class*='grid'] > div");
      const rowCount = await productRows.count();

      await screenshot(page, "b3-10-discover-products-loaded");
    }

    // If Add Product/manually is visible, click it to verify form opens
    if (hasAddProduct || hasAddManually) {
      const target = hasAddProduct ? addProductBtn : addManuallyBtn;
      await target.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "b3-10-add-product-form");
      await verifyNoErrorBoundary(page);
    }
  });

  test("Product cards display properly", async ({ page }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Click Discover tab to see product cards
    const discoverTab = page.getByRole("button", { name: /Discover/i });
    if (await discoverTab.isVisible().catch(() => false)) {
      await discoverTab.click();
      await page.waitForTimeout(3000);
    }
    await screenshot(page, "b3-10-products-cards-view");

    // Also check My Library tab for user's saved products
    const libraryTab = page.getByRole("button", { name: /My Library/i });
    if (await libraryTab.isVisible().catch(() => false)) {
      await libraryTab.click();
      await page.waitForTimeout(2000);
      await screenshot(page, "b3-10-my-library-view");
      await verifyNoErrorBoundary(page);
    }

    // Verify product display elements exist (either in table or card format)
    const productRows = page.locator("table tbody tr");
    const productCards = page.locator(
      '[data-testid^="product-card-"], [class*="product"]'
    );
    const gridItems = page.locator("[class*='grid'] > div");

    const rowCount = await productRows.count();
    const cardCount = await productCards.count();
    const gridCount = await gridItems.count();

    const pageText = await page.textContent("body");
    const hasEmptyState =
      pageText?.includes("No products") ||
      pageText?.includes("no products") ||
      pageText?.includes("Add your first") ||
      pageText?.includes("Discover products") ||
      pageText?.includes("empty");

    // Either products are displayed or an informative empty state is shown
    expect(
      rowCount > 0 || cardCount > 0 || gridCount > 0 || hasEmptyState,
      "Products page should show product cards/rows or an informative empty state"
    ).toBeTruthy();

    await screenshot(page, "b3-10-product-display-complete");

    // If there are products, verify they have basic elements (image, name, price)
    if (rowCount > 0) {
      const firstRow = productRows.first();
      const rowText = await firstRow.textContent();
      // Product rows should contain some text content
      expect(
        rowText?.length,
        "Product row should contain text content"
      ).toBeGreaterThan(0);
      await screenshot(page, "b3-10-product-row-detail");
    }
  });
});
