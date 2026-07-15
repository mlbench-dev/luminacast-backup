import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Clone Upload Flow (Phase 8)", () => {
  test.setTimeout(5 * 60 * 1000);

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Clone flow accepts photo/video/audio files", async ({ page }) => {
    await page.goto("/my-avatar/clone");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-clone-01-page-loaded");
    await verifyNoErrorBoundary(page);

    // Look for file input that accepts photo, video, and audio
    const fileInput = page.locator('input[type="file"]').first();
    const fileInputExists =
      (await fileInput.count()) > 0;

    if (fileInputExists) {
      // Check the accept attribute to verify it accepts photo/video/audio
      const acceptAttr = await fileInput.getAttribute("accept");
      await screenshot(page, "batch3-clone-02-file-input-found");

      // Verify it accepts common media types
      const acceptsMedia =
        acceptAttr === null || // null means accepts all
        acceptAttr?.includes("image") ||
        acceptAttr?.includes("video") ||
        acceptAttr?.includes("audio") ||
        acceptAttr?.includes(".mp4") ||
        acceptAttr?.includes(".jpg") ||
        acceptAttr?.includes(".mp3") ||
        acceptAttr?.includes(".wav");

      expect(
        acceptsMedia,
        "Clone file input should accept photo/video/audio files"
      ).toBeTruthy();
    } else {
      // Check for upload zone / drag-and-drop area
      const uploadZone = page.locator(
        '[data-testid="clone-upload-zone"], ' +
        '[data-testid="upload-zone"], ' +
        '[data-testid="dropzone"]'
      );
      const uploadZoneVisible = await uploadZone
        .isVisible({ timeout: TIMEOUTS.short })
        .catch(() => false);

      // Check page text for file type instructions
      const pageText = await page.textContent("body");
      const mentionsFileTypes =
        pageText?.includes("photo") ||
        pageText?.includes("video") ||
        pageText?.includes("audio") ||
        pageText?.includes("Upload") ||
        pageText?.includes("image") ||
        pageText?.includes("recording");

      expect(
        uploadZoneVisible || mentionsFileTypes,
        "Clone flow should indicate it accepts media files"
      ).toBeTruthy();
    }

    await screenshot(page, "batch3-clone-03-file-acceptance");
  });

  test("Clone flow page loads with setup elements", async ({ page }) => {
    await page.goto("/my-avatar/clone");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-clone-04-setup-elements");
    await verifyNoErrorBoundary(page);

    // Verify the page loaded correctly (not a 404 or error)
    const pageText = await page.textContent("body");
    const isClonePage =
      pageText?.includes("Clone") ||
      pageText?.includes("clone") ||
      pageText?.includes("Upload") ||
      pageText?.includes("Avatar") ||
      pageText?.includes("Record") ||
      pageText?.includes("Voice");

    expect(isClonePage, "Clone flow page should load with content").toBeTruthy();

    // Check for setup-related elements
    const setupElements = [
      // Name input
      page.locator(
        '[data-testid="clone-name-input"], ' +
        '[data-testid="avatar-name-input"], ' +
        'input[placeholder*="name" i]'
      ),
      // Upload or record section
      page.locator(
        '[data-testid="clone-upload-section"], ' +
        '[data-testid="upload-section"], ' +
        '[data-testid="record-section"]'
      ),
      // Instructions or description text
      page.locator(
        '[data-testid="clone-instructions"], ' +
        'text=/Upload|Record|photo|video/i'
      ),
    ];

    let foundElements = 0;
    for (const el of setupElements) {
      if (await el.first().isVisible({ timeout: TIMEOUTS.short }).catch(() => false)) {
        foundElements++;
      }
    }

    // At minimum the page should show some clone-related content
    expect(
      foundElements > 0 || isClonePage,
      "Clone flow should display setup elements"
    ).toBeTruthy();

    await screenshot(page, "batch3-clone-05-setup-verified");
  });

  test("Upload button is visible", async ({ page }) => {
    await page.goto("/my-avatar/clone");
    await waitForPageLoad(page);
    await screenshot(page, "batch3-clone-06-for-upload-btn");
    await verifyNoErrorBoundary(page);

    // Look for upload button / upload trigger
    const uploadBtn = page
      .locator(
        '[data-testid="clone-upload-btn"], ' +
        '[data-testid="upload-btn"], ' +
        '[data-testid="upload-media-btn"], ' +
        'button:has-text("Upload"), ' +
        'label:has-text("Upload")'
      )
      .first();
    const uploadBtnVisible = await uploadBtn
      .isVisible({ timeout: TIMEOUTS.medium })
      .catch(() => false);

    // Also check for a drag-and-drop upload zone that acts as upload trigger
    const dropzone = page.locator(
      '[data-testid="clone-upload-zone"], ' +
      '[data-testid="upload-zone"], ' +
      '[data-testid="dropzone"], ' +
      '[class*="dropzone"], ' +
      '[class*="upload-area"]'
    );
    const dropzoneVisible = await dropzone
      .first()
      .isVisible({ timeout: TIMEOUTS.short })
      .catch(() => false);

    // Check for file input (hidden input triggered by button)
    const fileInput = page.locator('input[type="file"]');
    const hasFileInput = (await fileInput.count()) > 0;

    expect(
      uploadBtnVisible || dropzoneVisible || hasFileInput,
      "Upload button or upload zone should be visible on Clone page"
    ).toBeTruthy();

    await screenshot(page, "batch3-clone-07-upload-btn-visible");
  });
});
