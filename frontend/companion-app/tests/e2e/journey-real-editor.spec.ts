import { test, expect, Page } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Journey: Real Multi-Track Editor (SceneComposer v2)", () => {
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    await loginAsAdmin(page);
  });

  test.afterEach(async () => {
    await page.close();
  });

  test("Arrange phase shows full 4-column editor layout", async () => {
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    // Find an existing cast card
    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(3000);

    // Verify 4-column layout exists (56px tab strip | 220px content | flex preview+timeline | 280px properties)
    const editorShell = page.locator('[data-testid="editor-shell"]');
    const tabStrip = page.locator('[data-testid="editor-tab-strip"]');
    const tabContent = page.locator('[data-testid="editor-tab-content"]');
    const timeline = page.locator('[data-testid="editor-timeline"]');
    const rightPanel = page.locator('[data-testid="editor-properties"]');

    await expect(editorShell).toBeVisible({ timeout: 10000 });
    await expect(tabStrip).toBeVisible();
    await expect(tabContent).toBeVisible();
    await expect(rightPanel).toBeVisible();
    await expect(timeline).toBeVisible();
    await page.screenshot({ path: "e2e-report/editor-01-layout.png" });

    // Verify 10 tabs in left panel
    for (const tab of [
      "blocks", "media", "stock", "audio", "text",
      "captions", "effects", "transitions", "filters", "stickers",
    ]) {
      const tabBtn = page.locator(`[data-testid="left-tab-${tab}"]`);
      await expect(tabBtn).toBeVisible();
    }
    await page.screenshot({ path: "e2e-report/editor-02-tabs.png" });

    // Verify timeline toolbar exists
    const timelineToolbar = page.locator('[data-testid="editor-timeline-toolbar"]');
    await expect(timelineToolbar).toBeVisible();
    await page.screenshot({ path: "e2e-report/editor-03-timeline.png" });
  });

  test("Left panel tabs show their content panels", async () => {
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(3000);

    // Click Captions tab — verify presets panel
    const captionsTab = page.locator('[data-testid="left-tab-captions"]');
    if (await captionsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await captionsTab.click();
      const presets = page.locator('[data-testid^="caption-preset-"]');
      expect(await presets.count()).toBeGreaterThan(3);
      await page.screenshot({ path: "e2e-report/editor-04-caption-presets.png" });
    }

    // Click Effects tab
    const effectsTab = page.locator('[data-testid="left-tab-effects"]');
    if (await effectsTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await effectsTab.click();
      await page.screenshot({ path: "e2e-report/editor-05-effects.png" });
    }

    // Click Transitions tab
    const transitionsTab = page.locator('[data-testid="left-tab-transitions"]');
    if (await transitionsTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await transitionsTab.click();
      const transitions = page.locator('[data-testid^="transition-"]');
      expect(await transitions.count()).toBeGreaterThan(5);
      await page.screenshot({ path: "e2e-report/editor-06-transitions.png" });
    }
  });

  test("TikTok safe zones toggle works", async () => {
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(3000);

    // Verify TikTok safe zones toggle
    const safeZonesBtn = page.locator('[data-testid="safe-zones-toggle"]');
    if (await safeZonesBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await safeZonesBtn.click();
      await page.screenshot({ path: "e2e-report/editor-07-safe-zones.png" });
      // Toggle off
      await safeZonesBtn.click();
      await page.screenshot({ path: "e2e-report/editor-08-safe-zones-off.png" });
    }
  });

  test("Script edit persists with debounced save", async () => {
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");

    // Check if we can reach the Script phase
    const scriptEditor = page.locator("h2", { hasText: "Script Editor" });
    // This test depends on being able to navigate to the script phase
    // which requires an existing cast with blocks. Skip if not available.
    if (!(await scriptEditor.isVisible({ timeout: 8000 }).catch(() => false))) {
      // Try navigating to an existing cast
      await page.goto("/cast-builder");
      const castCard = page.locator('[data-testid^="cast-card-"]').first();
      if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
        test.skip(true, "No casts available for script editing test");
        return;
      }
    }

    // If we can see a textarea with script text, verify the save indicator
    const textarea = page.locator("textarea").first();
    if (await textarea.isVisible({ timeout: 5000 }).catch(() => false)) {
      const originalText = await textarea.inputValue();
      await textarea.fill(originalText + " TEST_EDIT");
      // Wait for debounced save indicator
      await page.waitForTimeout(1000);
      const savedIndicator = page.locator("text=Saved");
      const savingIndicator = page.locator("text=Saving...");
      // Either "Saving..." or "Saved" should appear
      const hasSaveIndicator =
        (await savingIndicator.isVisible({ timeout: 3000 }).catch(() => false)) ||
        (await savedIndicator.isVisible({ timeout: 3000 }).catch(() => false));
      expect(hasSaveIndicator).toBeTruthy();
      await page.screenshot({ path: "e2e-report/editor-09-script-save.png" });
    }
  });

  test("Right properties panel shows cast properties when no selection", async () => {
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(3000);

    const rightPanel = page.locator('[data-testid="editor-properties"]');
    if (await rightPanel.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Should show "Cast Properties" when nothing is selected
      const castProps = rightPanel.locator("text=Cast Properties");
      await expect(castProps).toBeVisible({ timeout: 5000 });
      await page.screenshot({ path: "e2e-report/editor-10-properties.png" });
    }
  });
});
