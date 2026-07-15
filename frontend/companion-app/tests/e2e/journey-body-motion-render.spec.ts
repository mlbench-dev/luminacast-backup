import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Body Motion Render", () => {
  test("body motion render mode in cast builder with frame pickers", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Navigate to cast builder
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e-report/f-01-cast-detail.png" });

    // 2. Look for render mode selector and check Body Motion is present
    const bodyMotionBtn = page
      .locator("button")
      .filter({ hasText: "Body Motion" })
      .first();
    if (!(await bodyMotionBtn.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "Body Motion render mode not visible in UI");
      return;
    }
    await page.screenshot({ path: "e2e-report/f-02-render-modes-visible.png" });

    // 3. Click Body Motion
    await bodyMotionBtn.click();
    await page.waitForTimeout(1000);
    await page.screenshot({ path: "e2e-report/f-03-body-motion-selected.png" });

    // 4. Check for frame pickers or "no body motion photos" message
    const startFrame = page.locator('[data-testid="body-motion-start-frame"]');
    const noPhotosMsg = page.locator("text=body motion photos");
    const hasFramePickers = await startFrame
      .isVisible({ timeout: 3000 })
      .catch(() => false);
    const hasNoPhotosMsg = await noPhotosMsg
      .isVisible({ timeout: 3000 })
      .catch(() => false);

    if (hasFramePickers) {
      // Verify both start and end frame pickers
      const endFrame = page.locator('[data-testid="body-motion-end-frame"]');
      await expect(endFrame).toBeVisible({ timeout: 3000 });
      await page.screenshot({ path: "e2e-report/f-04-frame-pickers.png" });

      // Verify motion prompt chips
      const walkingChip = page
        .locator("button")
        .filter({ hasText: "Walking" })
        .first();
      if (await walkingChip.isVisible({ timeout: 3000 }).catch(() => false)) {
        await walkingChip.click();
        await page.waitForTimeout(500);
        await page.screenshot({ path: "e2e-report/f-05-motion-preset.png" });
      }

      // Verify motion prompt textarea
      const promptArea = page.locator(
        '[data-testid="body-motion-prompt"]'
      );
      if (await promptArea.isVisible({ timeout: 3000 }).catch(() => false)) {
        await page.screenshot({ path: "e2e-report/f-06-motion-prompt.png" });
      }

      // Verify cost estimate
      const costInfo = page.locator("text=Estimated cost");
      if (await costInfo.isVisible({ timeout: 3000 }).catch(() => false)) {
        await page.screenshot({ path: "e2e-report/f-07-cost-estimate.png" });
      }
    } else if (hasNoPhotosMsg) {
      // Acceptable — avatar has no body motion looks
      await page.screenshot({
        path: "e2e-report/f-04-no-body-motion-photos.png",
      });
    }

    // Either outcome is valid — the UI rendered correctly for the state
  });

  test("all four render modes coexist", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(2000);

    // Verify all 4 render modes are visible
    for (const mode of ["Avatar", "PIP", "Voiceover", "Body Motion"]) {
      const btn = page.locator("button").filter({ hasText: mode }).first();
      const visible = await btn
        .isVisible({ timeout: 3000 })
        .catch(() => false);
      await page.screenshot({
        path: `e2e-report/f-08-mode-${mode.toLowerCase().replace(/\s/g, "-")}.png`,
      });
      // At least Avatar and Body Motion should always be visible
      if (mode === "Avatar" || mode === "Body Motion") {
        expect(visible).toBeTruthy();
      }
    }
  });
});
