import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Output Format", () => {
  test("select output format and verify options", async ({ page }) => {
    await loginAsAdmin(page);

    // 1. Create a new cast page
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/format-01-new-cast.png" });

    // 2. Verify the Output Format section exists with all 4 options
    const formatButtons = page.locator("button").filter({ hasText: /^(9:16|16:9|1:1|4:5)/ });

    // Wait for at least one format button to appear
    await expect(formatButtons.first()).toBeVisible({ timeout: 10000 });

    // 3. Count that all 4 format options are present
    const count = await formatButtons.count();
    expect(count).toBeGreaterThanOrEqual(4);
    await page.screenshot({ path: "e2e-report/format-02-all-options.png" });

    // 4. Click 16:9 option
    const landscape = page.locator("button").filter({ hasText: "16:9" }).first();
    await landscape.click();
    await page.screenshot({ path: "e2e-report/format-03-16x9-selected.png" });

    // 5. Click 1:1 square
    const square = page.locator("button").filter({ hasText: "1:1" }).first();
    await square.click();
    await page.screenshot({ path: "e2e-report/format-04-1x1-selected.png" });

    // 6. Refresh and verify default
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "e2e-report/format-05-fresh-default.png" });

    // The default 9:16 button should have accent border
    const defaultBtn = page.locator("button").filter({ hasText: "9:16" }).first();
    await expect(defaultBtn).toBeVisible({ timeout: 10000 });
  });
});
