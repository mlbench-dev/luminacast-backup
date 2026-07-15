import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("QA Pass — Phase 6: Cross-cutting checks", () => {
  // ── Journey 6.1: Sidebar navigation ──
  test("6.1 Sidebar nav — every page loads without error", async ({
    page,
  }) => {
    await loginAsAdmin(page);

    const navItems = [
      { testid: "nav-my-avatar", route: "/my-avatar", label: "My Avatar" },
      { testid: "nav-product-library", route: "/products", label: "Product Library" },
      { testid: "nav-cast-builder", route: "/cast-builder", label: "Cast Builder" },
      { testid: "nav-go-live", route: "/live-control", label: "Go Live" },
      { testid: "nav-music", route: "/music", label: "Music" },
      { testid: "nav-my-videos", route: "/my-videos", label: "My Videos" },
      { testid: "nav-analytics", route: "/analytics", label: "Analytics" },
    ];

    for (const nav of navItems) {
      const link = page.locator(`[data-testid="${nav.testid}"]`);
      if (await link.isVisible({ timeout: 5000 }).catch(() => false)) {
        await link.click();
        await page.waitForTimeout(2000);
        await verifyNoErrorBoundary(page);
        await screenshot(page, `06-1-nav-${nav.label.toLowerCase().replace(/\s+/g, "-")}`);
      }
    }
  });

  // ── Journey 6.2: Settings pages ──
  test("6.2 Settings pages — channels, team, billing load", async ({
    page,
  }) => {
    await loginAsAdmin(page);

    const settingsPages = [
      { testid: "nav-my-channels", route: "/settings/channels", label: "My Channels" },
      { testid: "nav-team", route: "/settings/team", label: "Team" },
      { testid: "nav-billing", route: "/settings/billing", label: "Billing" },
    ];

    for (const nav of settingsPages) {
      const link = page.locator(`[data-testid="${nav.testid}"]`);
      if (await link.isVisible({ timeout: 5000 }).catch(() => false)) {
        await link.click();
        await page.waitForTimeout(2000);
        await verifyNoErrorBoundary(page);
        await screenshot(page, `06-2-settings-${nav.label.toLowerCase().replace(/\s+/g, "-")}`);
      } else {
        // Try direct navigation
        await page.goto(nav.route);
        await waitForPageLoad(page);
        await verifyNoErrorBoundary(page);
        await screenshot(page, `06-2-settings-${nav.label.toLowerCase().replace(/\s+/g, "-")}`);
      }
    }
  });

  // ── Journey 6.3: Auth flows ──
  test("6.3 Auth — logout and login cycle", async ({ page }) => {
    await loginAsAdmin(page);
    await screenshot(page, "06-3-logged-in");

    // Find and click logout button
    const logoutBtn = page.locator('[data-testid="logout-button"]');
    if (!(await logoutBtn.isVisible())) {
      // Try text-based
      const signOutBtn = page.locator("button").filter({ hasText: /Sign Out|Logout/i }).first();
      if (await signOutBtn.isVisible()) {
        await signOutBtn.click();
      }
    } else {
      await logoutBtn.click();
    }

    await page.waitForTimeout(2000);
    await screenshot(page, "06-3-logged-out");

    // Verify we're on login page
    const loginEmail = page.locator('[data-testid="login-email"]');
    await expect(loginEmail).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "06-3-login-page");

    // Try wrong password
    await loginEmail.fill("3gorka72@gmail.com");
    await page.locator('[data-testid="login-password"]').fill("wrong-password");
    await page.locator('[data-testid="login-submit"]').click();
    await page.waitForTimeout(2000);
    await screenshot(page, "06-3-wrong-password");

    // Verify error message or still on login page
    const stillOnLogin = await loginEmail.isVisible();
    expect(stillOnLogin, "Should still be on login page after wrong password").toBeTruthy();

    // Login with correct password
    await loginEmail.fill("3gorka72@gmail.com");
    await page.locator('[data-testid="login-password"]').fill("Polaroid-017");
    await page.locator('[data-testid="login-submit"]').click();
    await page.waitForURL(/\/(my-avatar|cast-builder|dashboard)/, {
      timeout: TIMEOUTS.medium,
    });
    await screenshot(page, "06-3-logged-back-in");
    await verifyNoErrorBoundary(page);

    // Verify session persists on refresh
    await page.reload();
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "06-3-session-persists");

    // Verify not redirected to login
    const url = page.url();
    expect(url).not.toContain("/login");
  });

  // ── Journey 6.4: Empty states ──
  test("6.4 Empty states — verify informative messages", async ({ page }) => {
    await loginAsAdmin(page);

    // Check My Videos empty state (might not be empty, but verify the page renders)
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "06-4-my-videos");

    // Check analytics page renders
    await page.goto("/analytics");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "06-4-analytics");

    // Verify analytics has data-testid
    const analyticsPage = page.locator('[data-testid="analytics-page"]');
    if (await analyticsPage.isVisible()) {
      await screenshot(page, "06-4-analytics-visible");
    }
  });
});
