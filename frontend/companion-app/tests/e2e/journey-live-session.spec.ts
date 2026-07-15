import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Live Session", () => {
  test("create and manage a live session", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Navigate to Go Live
    await page.goto("/live-control");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/h-01-go-live-page.png" });

    // 2. Verify session creation form exists
    const avatarPicker = page
      .locator('[data-testid="avatar-picker"]')
      .first();
    await expect(avatarPicker).toBeVisible({ timeout: 10000 });
    await page.screenshot({ path: "e2e-report/h-02-session-form.png" });

    // 3. Verify product queue UI
    const productPicker = page
      .locator('[data-testid="product-picker"]')
      .first();
    await expect(productPicker).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: "e2e-report/h-03-product-queue.png" });

    // 4. Verify max duration selector
    const durationSelect = page
      .locator('[data-testid="duration-select"]')
      .first();
    await expect(durationSelect).toBeVisible({ timeout: 3000 });
    await page.screenshot({ path: "e2e-report/h-04-duration.png" });

    // 5. Verify output format selector
    const formatSelect = page
      .locator('[data-testid="output-format"]')
      .first();
    await expect(formatSelect).toBeVisible({ timeout: 3000 });
    await page.screenshot({ path: "e2e-report/h-05-format.png" });

    // 6. Verify voice style textarea
    const voiceStyle = page.locator('[data-testid="voice-style"]').first();
    await expect(voiceStyle).toBeVisible({ timeout: 3000 });

    // 7. Verify create session button exists (disabled without avatar + products)
    const createBtn = page
      .locator('[data-testid="create-session-button"]')
      .first();
    await expect(createBtn).toBeVisible({ timeout: 3000 });
    await page.screenshot({ path: "e2e-report/h-06-create-button.png" });
  });
});
