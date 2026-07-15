import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Try-On Look", () => {
  test("navigate to try-on tab and verify UI", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Navigate to avatar list
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/tryon-01-avatar-list.png" });

    // 2. Find an avatar with Edit profile link
    const editLink = page.locator("text=Edit profile").first();
    if (!(await editLink.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip(true, "No avatars available for editing");
      return;
    }
    await editLink.click();
    await page.waitForURL(/\/my-avatar\/.*\/edit/, { timeout: 10000 });
    await page.screenshot({ path: "e2e-report/tryon-02-edit-page.png" });

    // 3. Switch to Try-On tab
    const tryonTab = page.locator("button:has-text(\"Try-On\")").first();
    await expect(tryonTab).toBeVisible({ timeout: 5000 });
    await tryonTab.click();
    await page.screenshot({ path: "e2e-report/tryon-03-tryon-tab.png" });

    // 4. Verify Add Look button exists
    const addButton = page.locator("button:has-text(\"Add Look\")").first();
    await expect(addButton).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: "e2e-report/tryon-04-add-button.png" });

    // 5. Navigate to Cast Builder to verify it loads
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/tryon-05-cast-builder.png" });

    // Verify cast builder loaded — look for any heading or form element
    const heading = page.locator("h2").first();
    await expect(heading).toBeVisible({ timeout: 10000 });
  });
});
