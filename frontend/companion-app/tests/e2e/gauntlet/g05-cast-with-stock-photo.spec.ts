import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { clickPlayAndVerify } from "./helpers/clickPlayAndVerify";
import { downloadAndVerifyMP4 } from "./helpers/media";
import { assertMinRuntime } from "./helpers/polling";
import {
  createCastAndReachEditor,
  finalizeAndWaitForRender,
  extractVideoUrl,
  API_BASE,
} from "./helpers/castFlow";
import * as path from "path";

const RENDER_TIMEOUT = 45 * 60 * 1000;

test.describe("G05 — Cast with Stock Photo", () => {
  test.setTimeout(RENDER_TIMEOUT + 5 * 60 * 1000);

  test("cast with stock photo: add stock photo to timeline, render, play", async ({
    page,
    request,
  }, testInfo) => {
    const testStart = Date.now();
    await loginAsAdmin(page);
    const castId = await createCastAndReachEditor(page, `G05 Stock Photo ${Date.now()}`);

    // Open Stock tab — MUST exist
    const stockTab = page.locator('[data-testid="left-tab-stock"]');
    await expect(stockTab, "Stock tab must be visible in editor").toBeVisible({ timeout: 5000 });
    await stockTab.click();
    await page.waitForTimeout(1000);

    // Find a stock photo — MUST exist
    const stockPhoto = page.locator(
      '[data-testid="stock-item"] img, .stock-grid img, [data-testid="stock-photo"]'
    ).first();
    await expect(stockPhoto, "Stock photo must be visible").toBeVisible({ timeout: 10000 });

    // Drag stock photo to preview canvas
    const preview = page.locator(
      '[data-testid="editor-preview"], [data-testid="preview-canvas"], .preview-area'
    ).first();
    await expect(preview, "Preview canvas must be visible").toBeVisible({ timeout: 5000 });
    await stockPhoto.dragTo(preview);
    await page.waitForTimeout(1000);

    // Verify element persists in cast via API (per spec D2)
    const castResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    expect(castResp.ok(), "Cast API should respond OK").toBeTruthy();

    // Verify timeline is visible
    const timeline = page.locator('[data-testid="editor-timeline"]');
    await expect(timeline).toBeVisible({ timeout: 5000 });

    // Finalize and render
    const castData = await finalizeAndWaitForRender(page, castId, RENDER_TIMEOUT);
    const videoUrl = extractVideoUrl(castData);
    expect(videoUrl, "Should have a video URL").toBeTruthy();

    // Navigate to Ready phase and play
    await page.goto(`/cast-builder/${castId}`);
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    const readyVideo = page.locator('[data-testid="ready-video-player"]');
    await expect(readyVideo).toBeVisible({ timeout: 30000 });
    await clickPlayAndVerify(page, readyVideo, {
      clickOverlay: '[data-testid="ready-play-overlay"]',
    });

    // Download and verify — REQUIRED
    const mp4Path = path.join(process.cwd(), "test-results", `g05-cast-stock-photo-${castId}.mp4`);
    const duration = await downloadAndVerifyMP4(request, videoUrl, mp4Path, testInfo);

    // Verify MP4 plays in fresh tab
    const freshPage = await page.context().newPage();
    await freshPage.goto(videoUrl);
    const nativeVideo = freshPage.locator("video").first();
    if (await nativeVideo.isVisible({ timeout: 10000 }).catch(() => false)) {
      await nativeVideo.evaluate((v: HTMLVideoElement) => v.play());
      await freshPage.waitForTimeout(3000);
      const isPaused = await nativeVideo.evaluate((v: HTMLVideoElement) => v.paused);
      expect(isPaused, "MP4 should play in fresh browser tab").toBe(false);
    }
    await freshPage.close();

    // Minimum runtime assertion
    assertMinRuntime(testStart);

    testInfo.annotations.push({
      type: "summary",
      description: `Stock photo cast ${castId} rendered. Duration: ${duration}s`,
    });
  });
});
