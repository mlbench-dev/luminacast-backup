import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 1,
  workers: 1,
  timeout: 5 * 60 * 1000,
  reporter: [["html", { outputFolder: "e2e-report" }], ["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "https://www.luminacast.com",
    screenshot: "on",
    trace: "on-first-retry",
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "gauntlet",
      testDir: "./tests/e2e/gauntlet",
      use: {
        ...devices["Desktop Chrome"],
        trace: "on",
        video: "on",
        screenshot: "on",
      },
      fullyParallel: false,
      workers: 1,
      retries: 0,
      timeout: 60 * 60 * 1000, // 1 hour per test (render tests need 45 min)
    },
  ],
});
