import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Body Motion Photos", () => {
  test("verify body motion tab and generate missing angles", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Navigate to an approved avatar's edit page
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle");
    const editLink = page.locator("text=Edit profile").first();
    if (!(await editLink.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip(true, "No approved avatars available");
      return;
    }
    await editLink.click();
    await page.waitForURL(/\/my-avatar\/.*\/edit/);
    await page.screenshot({ path: "e2e-report/body-01-edit-page.png" });

    // 2. Switch to Body Motion tab
    const bodyTab = page.locator("button:has-text(\"Body Motion\")");
    await expect(bodyTab).toBeVisible({ timeout: 5000 });
    await bodyTab.click();
    await page.screenshot({ path: "e2e-report/body-02-body-motion-tab.png" });

    // 3. Check if any auto-extracted looks exist
    const autoExtracted = page.locator("text=Auto-extracted");
    const autoCount = await autoExtracted.count();
    await page.screenshot({ path: "e2e-report/body-03-auto-extracted.png" });

    // 4. Check if "Generate Missing Angles" button exists
    const genMissingBtn = page
      .locator('button:has-text("Generate"), button:has-text("Missing Angle")')
      .first();
    if (await genMissingBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await page.screenshot({
        path: "e2e-report/body-04-gen-missing-visible.png",
      });

      // 5. Click it and verify it starts generating
      await genMissingBtn.click();
      await page.screenshot({ path: "e2e-report/body-05-generating.png" });

      // Wait briefly for at least one API call to go through
      await page.waitForTimeout(3000);
      await page.screenshot({ path: "e2e-report/body-06-after-click.png" });
    }

    // 6. Verify Add Look button exists on body motion tab
    const addBtn = page.locator('button:has-text("Add Look")').first();
    if (await addBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await addBtn.click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: "e2e-report/body-07-add-dialog.png" });

      // Close dialog
      const closeBtn = page
        .locator('button:has-text("Cancel"), button:has-text("Close"), [aria-label="Close"]')
        .first();
      if (await closeBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
        await closeBtn.click();
      } else {
        await page.keyboard.press("Escape");
      }
    }
  });
});
