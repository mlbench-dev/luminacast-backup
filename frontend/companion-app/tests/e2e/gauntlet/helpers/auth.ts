import { Page } from "@playwright/test";

export async function loginAsAdmin(page: Page): Promise<Page> {
  await page.goto("/login");
  await page.waitForSelector('[data-testid="login-email"]', { timeout: 15000 });
  await page.fill('[data-testid="login-email"]', "3gorka72@gmail.com");
  await page.fill('[data-testid="login-password"]', "Polaroid-017");
  await page.click('[data-testid="login-submit"]');
  await page.waitForURL(/\/(my-avatar|cast-builder|dashboard)/, { timeout: 15000 });
  return page;
}
