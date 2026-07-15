import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Live PIP via MuseTalk", () => {
  test("PIP engine selector shows Live option enabled", async ({ page }) => {
    await loginAsAdmin(page);

    // Check MuseTalk status
    const statusResp = await page.request.get("/api/system/musetalk-status");
    const status = await statusResp.json();

    if (!status.available) {
      test.skip(true, "MuseTalk not available");
      return;
    }

    // Open cast builder
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");
    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(2000);

    // Find PIP mode button and click it
    const pipBtn = page.locator('button:has-text("PIP")').first();
    if (await pipBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await pipBtn.click();
      await page.screenshot({ path: "e2e-report/g-01-pip-selected.png" });

      // Verify Live PIP option is enabled (not grayed out)
      const livePip = page.locator('button:has-text("Live PIP")').first();
      await expect(livePip).toBeVisible({ timeout: 5000 });

      const isDisabled = await livePip.getAttribute("disabled");
      expect(isDisabled).toBeNull(); // should NOT be disabled

      await page.screenshot({ path: "e2e-report/g-02-live-pip-enabled.png" });
    }
  });
});
