import { Page, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

export const TIMEOUTS = {
  short: 5_000,
  medium: 15_000,
  long: 60_000,
  render: 600_000,
};

const SCREENSHOT_DIR = "/home/user/workspace/qa_screenshots";

export async function waitForToast(page: Page, text: string | RegExp) {
  await expect(
    page
      .locator('[data-testid="toast"], [role="status"], [role="alert"]')
      .filter({ hasText: text })
  ).toBeVisible({ timeout: TIMEOUTS.medium });
}

export async function screenshot(page: Page, name: string) {
  if (!fs.existsSync(SCREENSHOT_DIR)) {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  }
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `${name}.png`),
    fullPage: true,
  });
}

export async function verifyButtonVisible(
  page: Page,
  selector: string,
  label: string
) {
  const btn = page.locator(selector).first();
  await expect(btn, `Button "${label}" should be visible`).toBeVisible({
    timeout: TIMEOUTS.medium,
  });
  await expect(btn, `Button "${label}" should be enabled`).toBeEnabled();
  return btn;
}

export async function verifyNoErrorBoundary(page: Page) {
  const errorTexts = [
    "Something went wrong",
    "is not defined",
    "Cannot read properties",
    "Error boundary",
    "Failed to render",
  ];
  for (const txt of errorTexts) {
    const found = await page.locator(`text=${txt}`).count();
    expect(found, `Error boundary detected: "${txt}"`).toBe(0);
  }
}

export async function waitForPageLoad(page: Page) {
  await page.waitForLoadState("networkidle", { timeout: TIMEOUTS.long });
}
