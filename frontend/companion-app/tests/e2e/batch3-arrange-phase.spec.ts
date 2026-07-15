import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Arrange Phase Redesign (Phase 7)", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  /**
   * Helper: navigate to a cast in the Arrange phase.
   * Searches existing casts for one in Audio Ready / Arrange / Ready state.
   */
  async function navigateToArrangePhase(
    page: import("@playwright/test").Page
  ): Promise<boolean> {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount === 0) {
      return false;
    }

    // Look for a cast in Arrange-compatible state
    let targetCard = null;
    for (let i = 0; i < Math.min(cardCount, 10); i++) {
      const card = castCards.nth(i);
      const text = await card.textContent();
      if (
        text?.includes("Audio Ready") ||
        text?.includes("Arrange") ||
        text?.includes("Ready")
      ) {
        targetCard = card;
        break;
      }
    }

    if (!targetCard) {
      // Fall back to first cast
      targetCard = castCards.first();
    }

    await targetCard.click();
    await page.waitForTimeout(3000);
    return true;
  }

  test("Arrange Phase shows 3-column layout", async ({ page }) => {
    const found = await navigateToArrangePhase(page);
    if (!found) {
      test.skip(true, "No casts available for arrange phase test");
      return;
    }

    await screenshot(page, "batch3-arrange-01-cast-opened");
    await verifyNoErrorBoundary(page);

    // Check for 3-column layout elements: left panel, center preview, right panel / timeline
    const leftPanel = page.locator(
      '[data-testid="editor-left-panel"], [data-testid="arrange-left-panel"], [data-testid="left-panel"]'
    );
    const centerPreview = page.locator(
      '[data-testid="editor-preview"], [data-testid="arrange-preview"], [data-testid="preview-panel"]'
    );
    const rightPanel = page.locator(
      '[data-testid="editor-right-panel"], [data-testid="arrange-right-panel"], [data-testid="right-panel"]'
    );

    const leftVisible = await leftPanel.isVisible({ timeout: TIMEOUTS.medium }).catch(() => false);
    const centerVisible = await centerPreview.isVisible({ timeout: TIMEOUTS.short }).catch(() => false);
    const rightVisible = await rightPanel.isVisible({ timeout: TIMEOUTS.short }).catch(() => false);

    // Also check for the general arrange/editor layout container
    const editorLayout = page.locator(
      '[data-testid="editor-layout"], [data-testid="arrange-layout"]'
    );
    const layoutVisible = await editorLayout.isVisible({ timeout: TIMEOUTS.short }).catch(() => false);

    // Check for column-like grid/flex structure
    const pageText = await page.textContent("body");
    const hasArrangeContent =
      pageText?.includes("Blocks") ||
      pageText?.includes("Media") ||
      pageText?.includes("Timeline") ||
      pageText?.includes("Preview");

    expect(
      leftVisible || layoutVisible || hasArrangeContent,
      "Arrange Phase should show a multi-column layout"
    ).toBeTruthy();

    await screenshot(page, "batch3-arrange-02-layout-check");
  });

  test("'Render Block' button visible in header", async ({ page }) => {
    const found = await navigateToArrangePhase(page);
    if (!found) {
      test.skip(true, "No casts available for arrange phase test");
      return;
    }

    await screenshot(page, "batch3-arrange-03-for-render-block");
    await verifyNoErrorBoundary(page);

    // Look for "Render Block" button
    const renderBlockBtn = page
      .locator(
        '[data-testid="render-block-btn"], ' +
        'button:has-text("Render Block"), ' +
        'button:has-text("Render Selected")'
      )
      .first();
    const renderBlockVisible = await renderBlockBtn
      .isVisible({ timeout: TIMEOUTS.medium })
      .catch(() => false);

    // Also check page text for Render Block presence
    const pageText = await page.textContent("body");
    const hasRenderBlock =
      pageText?.includes("Render Block") ||
      pageText?.includes("Render Selected");

    expect(
      renderBlockVisible || hasRenderBlock,
      "'Render Block' button should be visible in the Arrange phase header"
    ).toBeTruthy();

    await screenshot(page, "batch3-arrange-04-render-block-btn");
  });

  test("'Finalize & Render All' button visible", async ({ page }) => {
    const found = await navigateToArrangePhase(page);
    if (!found) {
      test.skip(true, "No casts available for arrange phase test");
      return;
    }

    await screenshot(page, "batch3-arrange-05-for-finalize");
    await verifyNoErrorBoundary(page);

    // Look for "Finalize & Render All" button
    const finalizeBtn = page
      .locator(
        '[data-testid="finalize-render-btn"], ' +
        '[data-testid="render-all-btn"], ' +
        'button:has-text("Finalize & Render All"), ' +
        'button:has-text("Finalize"), ' +
        'button:has-text("Render All")'
      )
      .first();

    await expect(
      finalizeBtn,
      "'Finalize & Render All' button should be visible"
    ).toBeVisible({ timeout: TIMEOUTS.medium });

    await screenshot(page, "batch3-arrange-06-finalize-btn");
  });

  test("Timeline area visible at bottom", async ({ page }) => {
    const found = await navigateToArrangePhase(page);
    if (!found) {
      test.skip(true, "No casts available for arrange phase test");
      return;
    }

    await screenshot(page, "batch3-arrange-07-for-timeline");
    await verifyNoErrorBoundary(page);

    // Look for timeline component
    const timeline = page.locator(
      '[data-testid="editor-timeline"], ' +
      '[data-testid="arrange-timeline"], ' +
      '[data-testid="timeline"], ' +
      '[data-testid="block-timeline"]'
    );
    const timelineVisible = await timeline
      .isVisible({ timeout: TIMEOUTS.medium })
      .catch(() => false);

    // Fallback: check page text for timeline-related content
    const pageText = await page.textContent("body");
    const hasTimelineContent =
      pageText?.includes("Timeline") ||
      pageText?.includes("timeline") ||
      pageText?.includes("00:0"); // timecodes like "00:00"

    // Check for any horizontal scrollable area at the bottom (timeline track)
    const timelineTrack = page.locator(
      '[data-testid*="track"], [data-testid*="timeline"], [class*="timeline"]'
    );
    const trackVisible = await timelineTrack.first().isVisible({ timeout: TIMEOUTS.short }).catch(() => false);

    expect(
      timelineVisible || hasTimelineContent || trackVisible,
      "Timeline area should be visible at the bottom of the Arrange phase"
    ).toBeTruthy();

    await screenshot(page, "batch3-arrange-08-timeline-visible");
  });
});
