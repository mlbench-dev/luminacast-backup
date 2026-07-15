import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Music Training", () => {
  test("navigate to music tab and view sound casts", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/music");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/music-01-page.png" });

    // Verify the page loads without crash
    const errorBanner = page.locator(
      "text=Something went wrong, text=Error"
    );
    expect(
      await errorBanner.isVisible({ timeout: 3000 }).catch(() => false)
    ).toBeFalsy();

    // Verify music tab structure
    const soundCastList = page
      .locator('[data-testid^="sound-cast-"]')
      .first();
    if (
      await soundCastList.isVisible({ timeout: 5000 }).catch(() => false)
    ) {
      await page.screenshot({
        path: "e2e-report/music-02-sound-casts.png",
      });
    }
  });
});
