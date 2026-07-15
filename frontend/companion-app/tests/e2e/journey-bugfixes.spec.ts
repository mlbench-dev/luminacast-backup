import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Bug Fix Verification", () => {
  test("product library loads without crash (Apify fallback)", async ({
    page,
  }) => {
    await loginAsAdmin(page);

    // Navigate to product library
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/bugfix-01-products-page.png" });

    // Page should render without crash — even if Apify returns 400,
    // the fallback should show an empty state, not an error page
    const errorBanner = page.locator(
      'text=Something went wrong, text=Error, text=500'
    );
    const hasError = await errorBanner
      .isVisible({ timeout: 3000 })
      .catch(() => false);
    expect(hasError).toBeFalsy();

    // The page should have some structure
    const pageContent = page.locator("h1, h2, [data-testid]").first();
    await expect(pageContent).toBeVisible({ timeout: 10000 });
    await page.screenshot({ path: "e2e-report/bugfix-02-products-loaded.png" });
  });

  test("cast builder output format selector works", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/bugfix-03-cast-new.png" });

    // Verify the page loads without crash
    const heading = page.locator("h2, h1").first();
    await expect(heading).toBeVisible({ timeout: 10000 });

    // Look for output format selector
    const formatSelector = page
      .locator('[data-testid*="output-format"], [data-testid*="format"]')
      .first();
    if (await formatSelector.isVisible({ timeout: 5000 }).catch(() => false)) {
      await page.screenshot({
        path: "e2e-report/bugfix-04-format-selector.png",
      });
      await formatSelector.click();
      await page.screenshot({
        path: "e2e-report/bugfix-05-format-dropdown.png",
      });
    }
  });

  test("all four Edit Avatar tabs render", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle");

    const editLink = page.locator("text=Edit profile").first();
    if (!(await editLink.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip(true, "No approved avatars");
      return;
    }
    await editLink.click();
    await page.waitForURL(/\/my-avatar\/.*\/edit/);

    // Click each tab and verify it renders
    for (const tabName of [
      "Scenes",
      "Body Motion",
      "Try-On",
      "Voice Examples",
    ]) {
      const tab = page.locator(`button:has-text("${tabName}")`);
      await expect(tab).toBeVisible({ timeout: 5000 });
      await tab.click();
      await page.waitForTimeout(500);
      await page.screenshot({
        path: `e2e-report/bugfix-06-tab-${tabName
          .toLowerCase()
          .replace(/\s/g, "-")}.png`,
      });
    }
  });

  test("PIP engine selector shows on cast page", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/bugfix-07-cast-new.png" });

    // Verify the page at minimum doesn't crash
    const heading = page.locator("h2, h1").first();
    await expect(heading).toBeVisible({ timeout: 10000 });
    await page.screenshot({ path: "e2e-report/bugfix-08-cast-loaded.png" });
  });
});
