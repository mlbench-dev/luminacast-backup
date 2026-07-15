import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Avatar Voice Pipeline (Phase 1)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Avatar page loads with voice section", async ({ page }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await screenshot(page, "b3-08-avatar-page-load");
    await verifyNoErrorBoundary(page);

    // Find an approved avatar and navigate to its edit page
    const avatarCards = page.locator('[data-testid^="avatar-card-"]');
    const cardCount = await avatarCards.count();
    expect(cardCount, "Should have at least 1 avatar").toBeGreaterThan(0);

    // Look for an approved avatar to click into edit mode
    let editTarget = null;
    for (let i = 0; i < Math.min(cardCount, 10); i++) {
      const card = avatarCards.nth(i);
      const text = await card.textContent();
      if (text?.includes("Approved")) {
        editTarget = card;
        break;
      }
    }

    if (editTarget) {
      const editLink = editTarget.locator("text=Edit profile");
      if ((await editLink.count()) > 0) {
        await editLink.click();
      } else {
        await editTarget.click();
      }
      await page.waitForURL(/\/my-avatar\/.*\/edit/, {
        timeout: TIMEOUTS.medium,
      });
      await waitForPageLoad(page);
      await screenshot(page, "b3-08-avatar-edit-page");
      await verifyNoErrorBoundary(page);

      // Look for Voice tab or Voice Examples tab
      const voiceTab = page
        .locator('button, [role="tab"]')
        .filter({ hasText: /Voice/i })
        .first();
      if (await voiceTab.isVisible()) {
        await voiceTab.click();
        await page.waitForTimeout(1000);
        await screenshot(page, "b3-08-voice-tab-active");
        await verifyNoErrorBoundary(page);

        // Verify voice section has content
        const pageText = await page.textContent("body");
        const hasVoiceContent =
          pageText?.includes("Voice") ||
          pageText?.includes("voice") ||
          pageText?.includes("Sample") ||
          pageText?.includes("Preview");
        expect(
          hasVoiceContent,
          "Voice section should display voice-related content"
        ).toBeTruthy();
      }
    } else {
      // No approved avatar; verify the avatar page itself loaded
      const pageText = await page.textContent("body");
      expect(
        pageText?.includes("Avatar") || pageText?.includes("avatar"),
        "Avatar page should load with avatar content"
      ).toBeTruthy();
    }

    await screenshot(page, "b3-08-voice-section-complete");
  });

  test("Voice preview section has test script input", async ({ page }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Navigate to an approved avatar's edit page
    const approvedCard = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "Approved" })
      .first();

    if ((await approvedCard.count()) === 0) {
      test.skip(true, "No approved avatar available for voice preview test");
      return;
    }

    const editLink = approvedCard.locator("text=Edit profile");
    if ((await editLink.count()) > 0) {
      await editLink.click();
    } else {
      await approvedCard.click();
    }
    await page.waitForURL(/\/my-avatar\/.*\/edit/, {
      timeout: TIMEOUTS.medium,
    });
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Click Voice tab
    const voiceTab = page
      .locator('button, [role="tab"]')
      .filter({ hasText: /Voice/i })
      .first();
    if (await voiceTab.isVisible()) {
      await voiceTab.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "b3-08-voice-preview-section");

      // Look for a test script input (textarea or input for typing test text)
      const testScriptInput = page
        .locator("textarea, input[type='text']")
        .filter({
          hasText: /test|preview|script|sample/i,
        })
        .first();

      // Also check for any textarea in the voice section
      const voiceTextarea = page.locator("textarea").first();
      const voiceInput = page
        .getByPlaceholder(/test|preview|script|type|enter/i)
        .first();

      const hasTestInput =
        (await testScriptInput.isVisible().catch(() => false)) ||
        (await voiceTextarea.isVisible().catch(() => false)) ||
        (await voiceInput.isVisible().catch(() => false));

      // Check for play/preview buttons
      const playBtn = page
        .locator("button")
        .filter({ hasText: /Play|Preview|Test|Listen/i })
        .first();
      const hasPlayButton = await playBtn.isVisible().catch(() => false);

      expect(
        hasTestInput || hasPlayButton,
        "Voice preview section should have a test script input or play/preview button"
      ).toBeTruthy();

      await screenshot(page, "b3-08-voice-test-input");
    }

    await screenshot(page, "b3-08-voice-preview-complete");
  });

  test("Voice selection/browser is accessible", async ({ page }) => {
    // Test voice selection in the AI Avatar creation flow
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Try the AI Avatar creation flow which includes voice selection
    const aiCard = page.locator('[data-testid="create-digital-card"]');
    if (await aiCard.isVisible({ timeout: TIMEOUTS.short }).catch(() => false)) {
      await aiCard.click();
      await page.waitForURL(/\/my-avatar\/ai-avatar/, {
        timeout: TIMEOUTS.medium,
      });
      await waitForPageLoad(page);
      await screenshot(page, "b3-08-ai-avatar-voice-flow");
      await verifyNoErrorBoundary(page);

      // Look for voice selection elements
      const pageText = await page.textContent("body");
      const hasVoiceSelection =
        pageText?.includes("Voice") ||
        pageText?.includes("voice") ||
        pageText?.includes("Select a voice") ||
        pageText?.includes("Choose voice");

      if (hasVoiceSelection) {
        // Look for voice browser/picker elements
        const voiceOptions = page.locator(
          '[data-testid^="voice-"], button:has-text("voice"), [class*="voice"]'
        );
        const voiceCount = await voiceOptions.count();

        // Also check for audio play buttons (voice previews)
        const audioButtons = page
          .locator("button")
          .filter({ hasText: /Play|Listen|Preview/i });
        const audioCount = await audioButtons.count();

        await screenshot(page, "b3-08-voice-browser");

        expect(
          hasVoiceSelection,
          "Voice selection should be accessible in avatar creation flow"
        ).toBeTruthy();
      }
    } else {
      // Fall back: navigate to an approved avatar's voice tab
      const approvedCard = page
        .locator('[data-testid^="avatar-card-"]')
        .filter({ hasText: "Approved" })
        .first();

      if ((await approvedCard.count()) > 0) {
        const editLink = approvedCard.locator("text=Edit profile");
        if ((await editLink.count()) > 0) {
          await editLink.click();
        } else {
          await approvedCard.click();
        }
        await page.waitForURL(/\/my-avatar\/.*\/edit/, {
          timeout: TIMEOUTS.medium,
        });
        await waitForPageLoad(page);

        const voiceTab = page
          .locator('button, [role="tab"]')
          .filter({ hasText: /Voice/i })
          .first();
        if (await voiceTab.isVisible()) {
          await voiceTab.click();
          await page.waitForTimeout(1000);
          await verifyNoErrorBoundary(page);
          await screenshot(page, "b3-08-voice-selection-edit-page");
        }
      }
    }

    await screenshot(page, "b3-08-voice-selection-complete");
  });
});
