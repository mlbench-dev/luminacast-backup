import { test, expect } from "@playwright/test";

test.describe("Live Control (Cockpit)", () => {
  test("should render live control page", async ({ page }) => {
    await page.goto("/live");
    // Would verify cockpit UI after auth
  });

  test("should show pre-live state when not streaming", async ({ page }) => {
    await page.goto("/live");
    // Would check for "Not Streaming" message after auth
  });

  test("should have cast selector and RTMP input", async ({ page }) => {
    await page.goto("/live");
    // Would check for cast-select and rtmp-input after auth
  });

  test("should display now-playing bar when live", async ({ page }) => {
    await page.goto("/live");
    // Would verify now-playing-bar visibility during live stream
  });

  test("should show chat panel", async ({ page }) => {
    await page.goto("/live");
    // Would verify chat-panel existence during live
  });

  test("should display AFK warning when product needs pinning", async ({ page }) => {
    await page.goto("/live");
    // Would verify afk-warning displays with countdown
  });
});
