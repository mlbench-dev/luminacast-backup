import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — BlockType/BlockCategory (Phase 6.1)", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  /**
   * Helper: navigate into a cast that has script blocks visible.
   * Tries existing casts first; falls back to creating a new one.
   */
  async function navigateToScriptPhase(page: import("@playwright/test").Page) {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount > 0) {
      // Prefer a cast already in Script or later phase
      let targetCard = null;
      for (let i = 0; i < Math.min(cardCount, 10); i++) {
        const card = castCards.nth(i);
        const text = await card.textContent();
        if (
          text?.includes("Script") ||
          text?.includes("Audio") ||
          text?.includes("Arrange") ||
          text?.includes("Ready")
        ) {
          targetCard = card;
          break;
        }
      }
      if (!targetCard) targetCard = castCards.first();

      await targetCard.click();
      await page.waitForTimeout(3000);
    } else {
      // Create new cast and generate a script
      await page.goto("/cast-builder/new");
      await waitForPageLoad(page);

      const nameInput = page.getByPlaceholder(/My Awesome Cast|cast name/i);
      if (await nameInput.isVisible({ timeout: TIMEOUTS.short })) {
        await nameInput.fill(`QA-blocks-${Date.now()}`);
      }

      const avatarButtons = page
        .locator("button")
        .filter({ has: page.locator("img") });
      if ((await avatarButtons.count()) > 0) {
        await avatarButtons.first().click();
        await page.waitForTimeout(500);
      }

      const generateBtn = page
        .locator("button")
        .filter({ hasText: /Generate Script/i })
        .first();
      if (await generateBtn.isVisible({ timeout: TIMEOUTS.short })) {
        await generateBtn.click();
        await page.waitForTimeout(10000);
      }
    }
  }

  test("Script Phase shows block type badges", async ({ page }) => {
    await navigateToScriptPhase(page);
    await screenshot(page, "batch3-blocks-01-script-phase");
    await verifyNoErrorBoundary(page);

    // Look for block type badges (e.g., "Hook", "Body", "CTA", "Intro", "Outro")
    const blockTypeBadges = [
      "Hook",
      "Body",
      "CTA",
      "Intro",
      "Outro",
      "Transition",
    ];
    const pageText = await page.textContent("body");

    let foundBadges = 0;
    for (const badge of blockTypeBadges) {
      if (pageText?.includes(badge)) {
        foundBadges++;
      }
    }

    // Also check for data-testid block type badges
    const badgeElements = page.locator(
      '[data-testid^="block-type-badge-"], [data-testid^="block-badge-"]'
    );
    const badgeCount = await badgeElements.count();

    // Check for any badge-like elements with block type text
    const allBadges = page
      .locator("span, div")
      .filter({ hasText: /^(Hook|Body|CTA|Intro|Outro|Transition)$/ });
    const allBadgeCount = await allBadges.count();

    expect(
      foundBadges > 0 || badgeCount > 0 || allBadgeCount > 0,
      "Script Phase should display block type badges"
    ).toBeTruthy();

    await screenshot(page, "batch3-blocks-02-type-badges");
  });

  test("Script Phase shows category badges (purple)", async ({ page }) => {
    await navigateToScriptPhase(page);
    await screenshot(page, "batch3-blocks-03-for-categories");
    await verifyNoErrorBoundary(page);

    // Look for category badges — these are purple-styled badges
    const categoryTerms = [
      "Avatar",
      "Voiceover",
      "B-Roll",
      "PiP",
      "Stock",
      "Media",
    ];
    const pageText = await page.textContent("body");

    let foundCategories = 0;
    for (const cat of categoryTerms) {
      if (pageText?.includes(cat)) {
        foundCategories++;
      }
    }

    // Check data-testid for category badges
    const categoryBadges = page.locator(
      '[data-testid^="block-category-badge-"], [data-testid^="category-badge-"]'
    );
    const categoryCount = await categoryBadges.count();

    // Check for purple-styled badge elements (background color indicators)
    const purpleBadges = page.locator(
      '.bg-purple-100, .bg-purple-500, .bg-violet-100, .bg-violet-500, [class*="purple"], [class*="violet"]'
    );
    const purpleCount = await purpleBadges.count();

    expect(
      foundCategories > 0 || categoryCount > 0 || purpleCount > 0,
      "Script Phase should display category badges"
    ).toBeTruthy();

    await screenshot(page, "batch3-blocks-04-category-badges");
  });

  test("Blocks display word count and duration estimates", async ({ page }) => {
    await navigateToScriptPhase(page);
    await screenshot(page, "batch3-blocks-05-for-word-count");
    await verifyNoErrorBoundary(page);

    const pageText = await page.textContent("body");

    // Check for word count indicators (e.g., "42 words", "words")
    const hasWordCount =
      /\d+\s*words?/i.test(pageText || "") ||
      pageText?.includes("words") ||
      pageText?.includes("word count");

    // Check for duration estimates (e.g., "~5s", "3.2s", "duration")
    const hasDuration =
      /~?\d+\.?\d*\s*s(ec)?/i.test(pageText || "") ||
      pageText?.includes("duration") ||
      pageText?.includes("sec");

    // Also check for data-testid elements
    const wordCountEl = page.locator(
      '[data-testid^="word-count"], [data-testid*="word-count"]'
    );
    const durationEl = page.locator(
      '[data-testid^="duration-estimate"], [data-testid*="duration"]'
    );

    const wordCountVisible = (await wordCountEl.count()) > 0;
    const durationVisible = (await durationEl.count()) > 0;

    expect(
      hasWordCount || wordCountVisible,
      "Blocks should display word count"
    ).toBeTruthy();

    expect(
      hasDuration || durationVisible,
      "Blocks should display duration estimates"
    ).toBeTruthy();

    await screenshot(page, "batch3-blocks-06-word-count-duration");
  });
});
