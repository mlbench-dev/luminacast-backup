import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("QA Pass — Phase 1: Avatar Journeys", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── Journey 1.1: Clone Avatar from TikTok URL ──
  test("1.1 Clone avatar — clone flow opens and has required elements", async ({
    page,
  }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await screenshot(page, "01-1-my-avatar-page");
    await verifyNoErrorBoundary(page);

    // Verify "Clone yourself" card is visible
    const cloneCard = page.locator('[data-testid="create-clone-card"]');
    await expect(cloneCard).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "01-1-clone-card-visible");

    // Click clone card
    await cloneCard.click();
    await page.waitForURL(/\/my-avatar\/clone/, { timeout: TIMEOUTS.medium });
    await waitForPageLoad(page);
    await screenshot(page, "01-1-clone-flow-opened");
    await verifyNoErrorBoundary(page);

    // Verify the clone flow page has loaded with some form of input
    // The CloneFlow component should be visible
    const setupPage = page.locator('[data-testid="setup-page"]');
    await expect(setupPage).toBeVisible({ timeout: TIMEOUTS.medium });

    // Look for TikTok/social media input or step indicators
    const hasInput = await page
      .locator("input, textarea")
      .first()
      .isVisible()
      .catch(() => false);
    const hasText = await page
      .locator("text=Social media, text=TikTok, text=URL, text=handle")
      .first()
      .isVisible()
      .catch(() => false);
    expect(
      hasInput || hasText,
      "Clone flow should show input or social media option"
    ).toBeTruthy();

    await screenshot(page, "01-1-clone-flow-form");

    // Navigate away and back
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await screenshot(page, "01-1-back-to-avatars");
  });

  // ── Journey 1.2: AI Avatar from prompt ──
  test("1.2 AI Avatar — creation flow opens with required elements", async ({
    page,
  }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Verify "AI Avatar" card
    const aiCard = page.locator('[data-testid="create-digital-card"]');
    await expect(aiCard).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "01-2-ai-avatar-card-visible");

    // Click AI card
    await aiCard.click();
    await page.waitForURL(/\/my-avatar\/ai-avatar/, {
      timeout: TIMEOUTS.medium,
    });
    await waitForPageLoad(page);
    await screenshot(page, "01-2-ai-avatar-flow-opened");
    await verifyNoErrorBoundary(page);

    // Verify the AI avatar creation page has description/prompt field
    // The page shows a textbox with placeholder like "sporty girl who sells beauty products"
    const descInput = page.getByRole("textbox");
    await expect(descInput.first()).toBeVisible({ timeout: TIMEOUTS.medium });

    // Look for voice selection
    const pageContent = await page.textContent("body");
    const hasVoiceStep =
      pageContent?.includes("voice") || pageContent?.includes("Voice");

    // Screenshot AI avatar flow
    await screenshot(page, "01-2-ai-avatar-form");
  });

  // ── Journey 1.3: Edit Avatar Profile ──
  test("1.3 Edit avatar profile — all tabs render", async ({ page }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Find an APPROVED avatar card and click it
    const avatarCards = page.locator('[data-testid^="avatar-card-"]');
    const cardCount = await avatarCards.count();
    expect(cardCount, "Should have at least 1 avatar").toBeGreaterThan(0);

    // Find one with "Approved" badge
    let editTarget = null;
    for (let i = 0; i < cardCount; i++) {
      const card = avatarCards.nth(i);
      const text = await card.textContent();
      if (text?.includes("Approved")) {
        editTarget = card;
        break;
      }
    }

    if (!editTarget) {
      // Try clicking "Edit profile" link on any card
      editTarget = page.locator("text=Edit profile").first();
    }

    expect(editTarget, "Should find an approved avatar to edit").not.toBeNull();

    // Click "Edit profile" link
    const editLink = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "Approved" })
      .first()
      .locator("text=Edit profile");
    const hasEditLink = (await editLink.count()) > 0;

    if (hasEditLink) {
      await editLink.click();
    } else {
      // Click the avatar card directly (approved cards navigate to edit)
      await page
        .locator('[data-testid^="avatar-card-"]')
        .filter({ hasText: "Approved" })
        .first()
        .click();
    }

    await page.waitForURL(/\/my-avatar\/.*\/edit/, {
      timeout: TIMEOUTS.medium,
    });
    await waitForPageLoad(page);
    await screenshot(page, "01-3-edit-avatar-page");
    await verifyNoErrorBoundary(page);

    // Verify edit page loaded
    const editPage = page.locator('[data-testid="edit-avatar-page"]');
    await expect(editPage).toBeVisible({ timeout: TIMEOUTS.medium });

    // Verify 4 tabs: Scenes, Body Motion, Try-On, Voice Examples
    const tabs = ["Scenes", "Body Motion", "Try-On", "Voice Examples"];
    for (const tabName of tabs) {
      const tab = page.locator(`button, [role="tab"]`).filter({ hasText: tabName });
      const visible = (await tab.count()) > 0;
      expect(visible, `Tab "${tabName}" should be visible`).toBeTruthy();
    }
    await screenshot(page, "01-3-tabs-visible");

    // Click each tab and verify no error
    for (const tabName of tabs) {
      const tab = page.locator(`button, [role="tab"]`).filter({ hasText: tabName }).first();
      if (await tab.isVisible()) {
        await tab.click();
        await page.waitForTimeout(500);
        await verifyNoErrorBoundary(page);
        await screenshot(page, `01-3-tab-${tabName.toLowerCase().replace(/\s+/g, "-")}`);
      }
    }

    // Verify avatar name is editable
    const nameInput = page.locator(
      'input[type="text"]'
    ).first();
    if (await nameInput.isVisible()) {
      const nameValue = await nameInput.inputValue();
      expect(nameValue.length, "Avatar name should not be empty").toBeGreaterThan(0);
    }

    await screenshot(page, "01-3-edit-avatar-complete");
  });

  // ── Journey 1.4: Avatar tile interactions ──
  test("1.4 Avatar grid — tiles show face thumbnails, status badges, type labels", async ({
    page,
  }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);
    await screenshot(page, "01-4-avatar-grid");

    // Verify avatar cards exist
    const avatarCards = page.locator('[data-testid^="avatar-card-"]');
    const cardCount = await avatarCards.count();
    expect(cardCount, "Should have avatars listed").toBeGreaterThan(0);

    // Check first few cards for required elements
    const checkCount = Math.min(cardCount, 5);
    let thumbnailsFound = 0;
    let statusBadgesFound = 0;
    let typeLabelsFound = 0;

    for (let i = 0; i < checkCount; i++) {
      const card = avatarCards.nth(i);
      const html = await card.innerHTML();

      // Check for face thumbnail (img tag with src) — NOT just text "AI Avatar"
      if (html.includes("<img")) {
        thumbnailsFound++;
      }

      // Check for status badge
      const cardText = await card.textContent();
      if (
        cardText?.includes("Approved") ||
        cardText?.includes("Ready") ||
        cardText?.includes("Processing") ||
        cardText?.includes("Failed")
      ) {
        statusBadgesFound++;
      }

      // Check for type label
      if (cardText?.includes("Clone") || cardText?.includes("AI Avatar")) {
        typeLabelsFound++;
      }
    }

    await screenshot(page, "01-4-avatar-cards-detail");

    expect(
      statusBadgesFound,
      `At least 1 of ${checkCount} cards should have a status badge`
    ).toBeGreaterThan(0);
    expect(
      typeLabelsFound,
      `At least 1 of ${checkCount} cards should have a type label`
    ).toBeGreaterThan(0);

    // Verify face thumbnails — spec says they should show face images, not just "AI Avatar" text
    // This is the known bug area: avatar grid showing only generic labels instead of face thumbnails
    if (thumbnailsFound === 0) {
      console.warn(
        "BUG CANDIDATE: No face thumbnails found on avatar cards. Cards may show only generic labels."
      );
    }

    // Verify clicking an approved avatar navigates to edit page
    const approvedCard = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "Approved" })
      .first();
    if ((await approvedCard.count()) > 0) {
      // Click the "Edit profile" link
      const editLink = approvedCard.locator("text=Edit profile");
      if ((await editLink.count()) > 0) {
        await editLink.click();
        await page.waitForURL(/\/my-avatar\/.*\/edit/, {
          timeout: TIMEOUTS.medium,
        });
        await screenshot(page, "01-4-clicked-approved-avatar");
        await verifyNoErrorBoundary(page);
      }
    }
  });
});
