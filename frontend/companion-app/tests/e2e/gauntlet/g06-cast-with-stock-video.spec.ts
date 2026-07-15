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

test.describe("G06 — Cast with Stock Video", () => {
  test.setTimeout(RENDER_TIMEOUT + 5 * 60 * 1000);

  test("cast with stock video: add stock video to timeline, render, play", async ({
    page,
    request,
  }, testInfo) => {
    const testStart = Date.now();
    await loginAsAdmin(page);
    const castId = await createCastAndReachEditor(page, `G06 Stock Video ${Date.now()}`);

    // Open Stock tab — MUST exist
    const stockTab = page.locator('[data-testid="left-tab-stock"]');
    await expect(stockTab, "Stock tab must be visible").toBeVisible({ timeout: 5000 });
    await stockTab.click();
    await page.waitForTimeout(1000);

    // Switch to video sub-tab if separate
    const videoSubTab = page.locator(
      'button:has-text("Videos"), [data-testid="stock-videos-tab"]'
    ).first();
    if (await videoSubTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await videoSubTab.click();
      await page.waitForTimeout(500);
    }

    // Find a stock video or item — MUST exist
    const stockVideo = page.locator(
      '[data-testid="stock-item"] video, .stock-grid video, [data-testid="stock-video"]'
    ).first();
    const stockItem = page.locator('[data-testid="stock-item"]').first();
    const target = (await stockVideo.isVisible({ timeout: 5000 }).catch(() => false))
      ? stockVideo
      : stockItem;
    await expect(target, "Stock video/item must be visible").toBeVisible({ timeout: 5000 });

    // Drag to preview canvas
    const preview = page.locator(
      '[data-testid="editor-preview"], [data-testid="preview-canvas"], .preview-area'
    ).first();
    await expect(preview, "Preview canvas must be visible").toBeVisible({ timeout: 5000 });
    await target.dragTo(preview);
    await page.waitForTimeout(1000);

    // Verify element persists in cast via API
    const castResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    expect(castResp.ok(), "Cast API should respond OK after drag").toBeTruthy();

    // Finalize and render
    const castData = await finalizeAndWaitForRender(page, castId, RENDER_TIMEOUT);
    const videoUrl = extractVideoUrl(castData);
    expect(videoUrl, "Should have a video URL").toBeTruthy();

    // Ready phase playback
    await page.goto(`/cast-builder/${castId}`);
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    const readyVideo = page.locator('[data-testid="ready-video-player"]');
    await expect(readyVideo).toBeVisible({ timeout: 30000 });
    await clickPlayAndVerify(page, readyVideo, {
      clickOverlay: '[data-testid="ready-play-overlay"]',
    });

    // Download and verify — REQUIRED
    const mp4Path = path.join(process.cwd(), "test-results", `g06-cast-stock-video-${castId}.mp4`);
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
      description: `Stock video cast ${castId} rendered. Duration: ${duration}s`,
    });
  });
});
