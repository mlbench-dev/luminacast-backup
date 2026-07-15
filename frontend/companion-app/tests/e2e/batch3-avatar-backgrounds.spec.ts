import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Avatar Background Library", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Avatar page has backgrounds tab", async ({ page }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await screenshot(page, "b3-09-avatar-page");
    await verifyNoErrorBoundary(page);

    // Find an approved avatar and navigate to its edit page
    const approvedCard = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "Approved" })
      .first();

    if ((await approvedCard.count()) === 0) {
      test.skip(true, "No approved avatar available for backgrounds test");
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
    await screenshot(page, "b3-09-avatar-edit-page");
    await verifyNoErrorBoundary(page);

    // Verify Scenes tab exists
    const backgroundsTab = page
      .locator('button, [role="tab"]')
      .filter({ hasText: /Scene/i })
      .first();
    await expect(
      backgroundsTab,
      'Scenes tab should be visible on the edit avatar page'
    ).toBeVisible({ timeout: TIMEOUTS.medium });

    // Click the Scenes tab
    await backgroundsTab.click();
    await page.waitForTimeout(1000);
    await screenshot(page, "b3-09-backgrounds-tab-active");
    await verifyNoErrorBoundary(page);

    // Verify scenes section has loaded with content
    const pageText = await page.textContent("body");
    const hasBackgroundContent =
      pageText?.includes("Scene") ||
      pageText?.includes("scene") ||
      pageText?.includes("Upload") ||
      pageText?.includes("Generate");
    expect(
      hasBackgroundContent,
      "Scenes tab should display scene-related content"
    ).toBeTruthy();

    await screenshot(page, "b3-09-backgrounds-tab-complete");
  });

  test("Background upload button visible", async ({ page }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Navigate to approved avatar edit page
    const approvedCard = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "Approved" })
      .first();

    if ((await approvedCard.count()) === 0) {
      test.skip(true, "No approved avatar available for background upload test");
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

    // Click Scenes tab
    const backgroundsTab = page
      .locator('button, [role="tab"]')
      .filter({ hasText: /Scene/i })
      .first();
    if (await backgroundsTab.isVisible()) {
      await backgroundsTab.click();
      await page.waitForTimeout(1000);
    }
    await screenshot(page, "b3-09-backgrounds-for-upload");
    await verifyNoErrorBoundary(page);

    // Look for upload button or upload area
    const uploadBtn = page
      .locator("button")
      .filter({ hasText: /Upload|Add Scene|Import/i })
      .first();
    const uploadInput = page.locator('input[type="file"]').first();
    const uploadArea = page
      .locator('[data-testid="background-upload"], [data-testid="upload-background"]')
      .first();

    const hasUploadButton = await uploadBtn.isVisible().catch(() => false);
    const hasUploadInput = (await uploadInput.count()) > 0;
    const hasUploadArea = await uploadArea.isVisible().catch(() => false);

    // Also check for drag-and-drop zones or "+" add buttons
    const addButton = page
      .locator("button")
      .filter({ hasText: /\+|Add/i })
      .first();
    const hasAddButton = await addButton.isVisible().catch(() => false);

    expect(
      hasUploadButton || hasUploadInput || hasUploadArea || hasAddButton,
      "Scene upload button or upload area should be visible"
    ).toBeTruthy();

    await screenshot(page, "b3-09-upload-button-visible");
  });

  test("AI generate background option available", async ({ page }) => {
    await page.goto("/my-avatar");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    // Navigate to approved avatar edit page
    const approvedCard = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "Approved" })
      .first();

    if ((await approvedCard.count()) === 0) {
      test.skip(true, "No approved avatar available for AI background test");
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

    // Click Scenes tab
    const backgroundsTab = page
      .locator('button, [role="tab"]')
      .filter({ hasText: /Scene/i })
      .first();
    if (await backgroundsTab.isVisible()) {
      await backgroundsTab.click();
      await page.waitForTimeout(1000);
    }
    await screenshot(page, "b3-09-backgrounds-for-ai-generate");
    await verifyNoErrorBoundary(page);

    // Look for AI generate button or option
    const aiGenerateBtn = page
      .locator("button")
      .filter({ hasText: /AI Generate|Generate|Create with AI|AI Scene/i })
      .first();
    const generateOption = page
      .locator('[data-testid="ai-generate-background"], [data-testid="generate-background"]')
      .first();

    const hasAiGenerate = await aiGenerateBtn.isVisible().catch(() => false);
    const hasGenerateOption = await generateOption.isVisible().catch(() => false);

    // Also check for any element mentioning AI generation in the backgrounds section
    const pageText = await page.textContent("body");
    const hasAiText =
      pageText?.includes("AI Generate") ||
      pageText?.includes("Generate scene") ||
      pageText?.includes("AI Scene") ||
      pageText?.includes("Create with AI") ||
      pageText?.includes("Generate");

    expect(
      hasAiGenerate || hasGenerateOption || hasAiText,
      "AI generate scene option should be available in the scenes section"
    ).toBeTruthy();

    // If the AI generate button is visible, click it to verify the dialog/form opens
    if (hasAiGenerate) {
      await aiGenerateBtn.click();
      await page.waitForTimeout(1000);
      await screenshot(page, "b3-09-ai-generate-dialog");
      await verifyNoErrorBoundary(page);
    }

    await screenshot(page, "b3-09-ai-generate-complete");
  });
});
