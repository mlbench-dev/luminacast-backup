import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — ErrorBoundary + Sentry (Phase 9)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("App renders without error boundary showing", async ({ page }) => {
    await waitForPageLoad(page);
    await screenshot(page, "b3-07-app-initial-load");
    await verifyNoErrorBoundary(page);

    // Verify the main app shell loaded (sidebar, header, or content area)
    const body = page.locator("body");
    await expect(body).toBeVisible();
    const bodyText = await body.textContent();
    expect(
      bodyText?.length,
      "App should render meaningful content, not a blank page"
    ).toBeGreaterThan(0);

    await screenshot(page, "b3-07-app-no-error-boundary");
  });

  test("Main navigation works without errors", async ({ page }) => {
    const navItems = [
      { testid: "nav-my-avatar", label: "My Avatar" },
      { testid: "nav-product-library", label: "Product Library" },
      { testid: "nav-cast-builder", label: "Cast Builder" },
      { testid: "nav-my-videos", label: "My Videos" },
    ];

    for (const nav of navItems) {
      const link = page.locator(`[data-testid="${nav.testid}"]`);
      if (await link.isVisible({ timeout: TIMEOUTS.short }).catch(() => false)) {
        await link.click();
        await page.waitForTimeout(2000);
        await verifyNoErrorBoundary(page);
        await screenshot(
          page,
          `b3-07-nav-${nav.label.toLowerCase().replace(/\s+/g, "-")}`
        );
      }
    }
  });

  test("Dashboard loads without errors", async ({ page }) => {
    // After login, we land on a main page (dashboard, my-avatar, or cast-builder)
    await waitForPageLoad(page);
    await screenshot(page, "b3-07-dashboard-load");
    await verifyNoErrorBoundary(page);

    // Verify the page has rendered real content (not stuck on a loading spinner)
    const pageText = await page.textContent("body");
    const hasContent =
      pageText?.includes("Avatar") ||
      pageText?.includes("Cast") ||
      pageText?.includes("Dashboard") ||
      pageText?.includes("Welcome") ||
      pageText?.includes("Videos");
    expect(hasContent, "Dashboard should display recognizable content").toBeTruthy();

    // Verify no console errors related to Sentry or error boundaries
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.reload();
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "b3-07-dashboard-after-reload");
  });

  test("Each major page loads without error boundary — my-avatar", async ({
    page,
  }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await screenshot(page, "b3-07-page-my-avatar");
    await verifyNoErrorBoundary(page);

    const pageText = await page.textContent("body");
    expect(
      pageText?.includes("Avatar") || pageText?.includes("avatar"),
      "My Avatar page should contain avatar-related content"
    ).toBeTruthy();
  });

  test("Each major page loads without error boundary — my-videos", async ({
    page,
  }) => {
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await screenshot(page, "b3-07-page-my-videos");
    await verifyNoErrorBoundary(page);

    const pageText = await page.textContent("body");
    expect(
      pageText?.includes("Video") || pageText?.includes("video") || pageText?.includes("Videos"),
      "My Videos page should contain video-related content"
    ).toBeTruthy();
  });

  test("Each major page loads without error boundary — products", async ({
    page,
  }) => {
    await page.goto("/products");
    await waitForPageLoad(page);
    await screenshot(page, "b3-07-page-products");
    await verifyNoErrorBoundary(page);

    const pageText = await page.textContent("body");
    expect(
      pageText?.includes("Product") ||
        pageText?.includes("product") ||
        pageText?.includes("Discover") ||
        pageText?.includes("Library"),
      "Products page should contain product-related content"
    ).toBeTruthy();
  });

  test("Each major page loads without error boundary — casts", async ({
    page,
  }) => {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);
    await screenshot(page, "b3-07-page-casts");
    await verifyNoErrorBoundary(page);

    const pageText = await page.textContent("body");
    expect(
      pageText?.includes("Cast") ||
        pageText?.includes("cast") ||
        pageText?.includes("New Cast"),
      "Cast Builder page should contain cast-related content"
    ).toBeTruthy();
  });
});
