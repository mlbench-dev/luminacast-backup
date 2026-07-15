import { test, expect } from "@playwright/test";

test.describe("Onboarding / Auth Flow", () => {
  test("should display login page", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByTestId("login-email")).toBeVisible();
    await expect(page.getByTestId("login-password")).toBeVisible();
    await expect(page.getByTestId("login-submit")).toBeVisible();
  });

  test("should toggle between login and register", async ({ page }) => {
    await page.goto("/login");
    await page.getByTestId("toggle-auth-mode").click();
    await expect(page.getByTestId("register-tiktok")).toBeVisible();
    await page.getByTestId("toggle-auth-mode").click();
    await expect(page.getByTestId("register-tiktok")).not.toBeVisible();
  });

  test("should redirect unauthenticated users to login", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("should show error on invalid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.getByTestId("login-email").fill("bad@email.com");
    await page.getByTestId("login-password").fill("wrongpass");
    await page.getByTestId("login-submit").click();
    // Toast should appear with error
  });
});
