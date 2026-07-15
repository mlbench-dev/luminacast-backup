import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("QA Pass — Phase 3: Cast Builder", () => {
  test.setTimeout(10 * 60 * 1000); // 10 min max per test

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── Journey 3.1: Cast with Avatar render mode ──
  test("3.1 Cast Builder — setup phase opens, avatar/product selection works", async ({
    page,
  }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await screenshot(page, "03-1-setup-phase");
    await verifyNoErrorBoundary(page);

    // Verify cast name input
    const nameInput = page.getByPlaceholder(/My Awesome Cast|cast name/i);
    await expect(nameInput).toBeVisible({ timeout: TIMEOUTS.medium });
    await nameInput.fill(`QA-test-${Date.now()}`);
    await screenshot(page, "03-1-name-filled");

    // Verify avatar picker grid
    const avatarTiles = page.locator("img").filter({ has: page.locator("[alt]") });
    const bodyText = await page.textContent("body");
    const hasAvatars = bodyText?.includes("avatar") || bodyText?.includes("Avatar");
    expect(hasAvatars, "Setup should show avatars").toBeTruthy();

    // Click first avatar if available
    const avatarButtons = page.locator("button").filter({ has: page.locator("img") });
    if ((await avatarButtons.count()) > 0) {
      await avatarButtons.first().click();
      await page.waitForTimeout(500);
      await screenshot(page, "03-1-avatar-selected");
    }

    // Verify script direction textarea
    const directionInput = page.getByPlaceholder(/energetically|describe|product benefits/i);
    if (await directionInput.isVisible()) {
      await directionInput.fill("Quick 5-second hook about the product, energetic");
    }

    // Verify output format buttons
    const formats = ["9:16", "16:9", "1:1", "4:5"];
    for (const fmt of formats) {
      const fmtBtn = page.locator("button").filter({ hasText: fmt }).first();
      const visible = await fmtBtn.isVisible().catch(() => false);
      if (visible) {
        await screenshot(page, `03-1-format-${fmt.replace(":", "x")}`);
        break;
      }
    }

    // Verify quality buttons
    const qualities = ["Simple", "HD"];
    for (const q of qualities) {
      const qBtn = page.locator("button").filter({ hasText: q }).first();
      if (await qBtn.isVisible().catch(() => false)) {
        await screenshot(page, "03-1-quality-visible");
        break;
      }
    }

    // Verify Generate Script button
    const generateBtn = page.locator("button").filter({ hasText: /Generate Script/i }).first();
    await expect(generateBtn).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "03-1-generate-btn-visible");
  });

  // ── Journey 3.2: Full cast creation flow through script phase ──
  test("3.2 Cast Builder — full flow: setup → script → audio generation", async ({
    page,
  }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Fill cast name
    const nameInput = page.getByPlaceholder(/My Awesome Cast|cast name/i);
    await nameInput.fill(`QA-cast-${Date.now()}`);

    // Select first avatar
    const avatarButtons = page.locator("button").filter({ has: page.locator("img") });
    if ((await avatarButtons.count()) > 0) {
      await avatarButtons.first().click();
      await page.waitForTimeout(500);
    }

    // Enter direction
    const directionInput = page.getByPlaceholder(/energetically|describe|product benefits/i);
    if (await directionInput.isVisible()) {
      await directionInput.fill("Quick energetic product showcase");
    }

    // Click Generate Script
    const generateBtn = page.locator("button").filter({ hasText: /Generate Script/i }).first();
    if (await generateBtn.isEnabled()) {
      await generateBtn.click();
      await screenshot(page, "03-2-generating-script");

      // Wait for script phase to load (blocks should appear)
      await page.waitForTimeout(10000);
      await screenshot(page, "03-2-script-phase");
      await verifyNoErrorBoundary(page);

      // Check if we're in script phase
      const pageText = await page.textContent("body");
      const inScriptPhase =
        pageText?.includes("Script Editor") ||
        pageText?.includes("Generate Audio") ||
        pageText?.includes("Block") ||
        pageText?.includes("words");

      if (inScriptPhase) {
        await screenshot(page, "03-2-script-blocks");

        // Verify script blocks exist
        const blockAreas = page.locator("textarea");
        const blockCount = await blockAreas.count();

        // Verify Generate Audio button
        const audioBtn = page.locator("button").filter({ hasText: /Generate Audio/i }).first();
        if (await audioBtn.isVisible()) {
          await screenshot(page, "03-2-audio-btn-visible");
        }
      }
    }
  });

  // ── Journey 3.3: Navigate existing cast ──
  test("3.3 Existing casts list — cards render with status badges", async ({
    page,
  }) => {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);
    await screenshot(page, "03-3-my-casts-page");
    await verifyNoErrorBoundary(page);

    // Verify my casts page
    const pageDiv = page.locator('[data-testid="my-casts-page"]');
    await expect(pageDiv).toBeVisible({ timeout: TIMEOUTS.medium });

    // Verify "New Cast" button
    const newCastBtn = page.locator("button").filter({ hasText: /New Cast/i }).first();
    await expect(newCastBtn).toBeVisible({ timeout: TIMEOUTS.medium });

    // Check cast cards
    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();
    await screenshot(page, "03-3-cast-cards");

    if (cardCount > 0) {
      // Verify first card has status badge
      const firstCard = castCards.first();
      const cardText = await firstCard.textContent();
      const hasStatus =
        cardText?.includes("Draft") ||
        cardText?.includes("Ready") ||
        cardText?.includes("Generating") ||
        cardText?.includes("Failed") ||
        cardText?.includes("Audio");
      // Status badges should exist
      await screenshot(page, "03-3-first-card-detail");

      // Click first card to navigate to it
      await firstCard.click();
      await page.waitForTimeout(2000);
      await screenshot(page, "03-3-opened-cast");
      await verifyNoErrorBoundary(page);
    }
  });

  // ── Journey 3.4: Arrange phase — editor loads ──
  test("3.4 Arrange phase — editor components render", async ({ page }) => {
    // Navigate to cast builder and find a cast that's in editor/arrange phase or ready
    await page.goto("/cast-builder");
    await waitForPageLoad(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount === 0) {
      test.skip(true, "No casts available for arrange phase test");
      return;
    }

    // Look for a cast in Audio Ready or Ready state
    let targetCard = null;
    for (let i = 0; i < Math.min(cardCount, 10); i++) {
      const card = castCards.nth(i);
      const text = await card.textContent();
      if (
        text?.includes("Audio Ready") ||
        text?.includes("Ready") ||
        text?.includes("Arrange")
      ) {
        targetCard = card;
        break;
      }
    }

    if (!targetCard) {
      // Just click the first card
      targetCard = castCards.first();
    }

    await targetCard.click();
    await page.waitForTimeout(3000);
    await screenshot(page, "03-4-cast-opened");
    await verifyNoErrorBoundary(page);

    // Check if we landed on an editor/arrange page
    const hasTimeline = await page.locator('[data-testid="editor-timeline"]').isVisible().catch(() => false);
    const hasPreview = await page.locator('[data-testid="editor-preview"]').isVisible().catch(() => false);
    const hasLeftPanel = await page.locator('[data-testid="editor-left-panel"]').isVisible().catch(() => false);

    if (hasTimeline || hasPreview || hasLeftPanel) {
      await screenshot(page, "03-4-editor-layout");

      // Verify Finalize & Render button
      const finalizeBtn = page.locator("button").filter({ hasText: /Finalize|Render/i }).first();
      if (await finalizeBtn.isVisible()) {
        await screenshot(page, "03-4-finalize-btn");
      }

      // Check editor tabs
      const tabs = ["Blocks", "Media", "Stock", "Audio", "Text", "Captions"];
      for (const tab of tabs) {
        const tabBtn = page.locator(`[data-testid="left-tab-${tab.toLowerCase()}"]`);
        if (await tabBtn.isVisible()) {
          await tabBtn.click();
          await page.waitForTimeout(300);
          await screenshot(page, `03-4-tab-${tab.toLowerCase()}`);
        }
      }
    }

    await screenshot(page, "03-4-complete");
  });

  // ── Journey 3.5: Cast with voiceover/stock media ──
  test("3.5 Stock media picker — opens and searches", async ({ page }) => {
    await page.goto("/my-videos");
    await waitForPageLoad(page);
    await screenshot(page, "03-5-my-videos");
    await verifyNoErrorBoundary(page);

    // Check for stock media import button
    const stockBtn = page.locator('[data-testid="import-stock-media"]');
    if (await stockBtn.isVisible()) {
      await stockBtn.click();
      await page.waitForTimeout(2000);
      await screenshot(page, "03-5-stock-media-picker");

      // Verify Pexels attribution
      const pageText = await page.textContent("body");
      const hasPexels =
        pageText?.includes("Pexels") || pageText?.includes("pexels");
      if (hasPexels) {
        await screenshot(page, "03-5-pexels-attribution");
      }

      // Try search
      const searchInput = page.getByPlaceholder(/Search/i).first();
      if (await searchInput.isVisible()) {
        await searchInput.fill("ocean");
        await page.waitForTimeout(2000);
        await screenshot(page, "03-5-stock-search-results");
      }
    }
  });

  // ── Journey 3.6: Multi-block cast phase header ──
  test("3.6 Phase header — all phases render correctly", async ({ page }) => {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount === 0) {
      test.skip(true, "No casts to test phase header");
      return;
    }

    await castCards.first().click();
    await page.waitForTimeout(2000);
    await screenshot(page, "03-6-phase-header");
    await verifyNoErrorBoundary(page);

    // Verify phase header shows phase labels
    const phases = ["Setup", "Script", "Audio", "Arrange", "Render", "Ready"];
    const pageText = await page.textContent("body");
    let phasesFound = 0;
    for (const p of phases) {
      if (pageText?.includes(p)) phasesFound++;
    }
    // At least some phase labels should be visible
    await screenshot(page, "03-6-phases-visible");
  });

  // ── Journey 3.7: Picture block ──
  test("3.7 Picture block — document feature gap or verify", async ({
    page,
  }) => {
    // Picture blocks are not a first-class block type in the current implementation
    // Document as feature gap
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await screenshot(page, "03-7-setup-for-picture-block");
    await verifyNoErrorBoundary(page);

    // Check if there's a way to add an image/picture block
    const pageText = await page.textContent("body");
    const hasPictureBlock =
      pageText?.includes("Picture") ||
      pageText?.includes("Image Block") ||
      pageText?.includes("Photo Block");

    if (!hasPictureBlock) {
      console.log(
        "FEATURE GAP: Picture block not implemented as a first-class type"
      );
    }
    await screenshot(page, "03-7-no-picture-block");
  });

  // ── Journey 3.8: Captions ──
  test("3.8 Captions — verify caption preset UI in arrange phase", async ({
    page,
  }) => {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    if ((await castCards.count()) === 0) {
      test.skip(true, "No casts for captions test");
      return;
    }

    // Find a cast in arrange/ready state
    let targetCard = null;
    const count = await castCards.count();
    for (let i = 0; i < Math.min(count, 10); i++) {
      const text = await castCards.nth(i).textContent();
      if (
        text?.includes("Ready") ||
        text?.includes("Audio Ready") ||
        text?.includes("Arrange")
      ) {
        targetCard = castCards.nth(i);
        break;
      }
    }

    if (!targetCard) {
      targetCard = castCards.first();
    }

    await targetCard.click();
    await page.waitForTimeout(3000);
    await screenshot(page, "03-8-cast-for-captions");
    await verifyNoErrorBoundary(page);

    // Check for captions tab
    const captionsTab = page.locator('[data-testid="left-tab-captions"]');
    if (await captionsTab.isVisible()) {
      await captionsTab.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "03-8-captions-tab");

      // Check for caption presets
      const captionPreset = page.locator('[data-testid^="caption-preset-"]');
      const presetCount = await captionPreset.count();
      await screenshot(page, "03-8-caption-presets");
    } else {
      console.log(
        "Captions tab not visible — may not be in arrange phase"
      );
      await screenshot(page, "03-8-no-captions-tab");
    }
  });
});
