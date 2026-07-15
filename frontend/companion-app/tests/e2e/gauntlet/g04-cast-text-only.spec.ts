import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { clickPlayAndVerify } from "./helpers/clickPlayAndVerify";
import { downloadAndVerifyMP4 } from "./helpers/media";
import { assertMinRuntime } from "./helpers/polling";
import {
  createCastAndReachEditor,
  finalizeAndWaitForRender,
  extractVideoUrl,
} from "./helpers/castFlow";
import * as path from "path";

const RENDER_TIMEOUT = 45 * 60 * 1000;

test.describe("G04 — Cast Text-Only (Minimal)", () => {
  test.setTimeout(RENDER_TIMEOUT + 5 * 60 * 1000);

  test("text-only cast: setup → script → audio → arrange → finalize → play", async ({
    page,
    request,
  }, testInfo) => {
    const testStart = Date.now();
    await loginAsAdmin(page);
    const castId = await createCastAndReachEditor(page, `G04 Text Only ${Date.now()}`);

    // Verify editor fits one viewport
    const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    const viewportHeight = page.viewportSize()?.height || 720;
    expect(pageHeight, "Editor should fit one viewport").toBeLessThanOrEqual(viewportHeight + 50);

    // Click play in editor preview
    const playBtn = page.locator('[data-testid="playback-play-pause"]');
    await expect(playBtn).toBeVisible({ timeout: 10000 });
    await playBtn.click();
    await page.waitForTimeout(1500);
    await playBtn.click(); // pause

    // Finalize and wait for render — requires status=ready + final_video_url
    const castData = await finalizeAndWaitForRender(page, castId, RENDER_TIMEOUT);
    const videoUrl = extractVideoUrl(castData);
    expect(videoUrl, "Should have a video URL").toBeTruthy();

    // Navigate to Ready phase
    await page.goto(`/cast-builder/${castId}`);
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    // Play the Ready video
    const readyVideo = page.locator('[data-testid="ready-video-player"]');
    await expect(readyVideo).toBeVisible({ timeout: 30000 });
    await clickPlayAndVerify(page, readyVideo, {
      clickOverlay: '[data-testid="ready-play-overlay"]',
    });

    // Download and verify MP4 — REQUIRED artifact
    const mp4Path = path.join(process.cwd(), "test-results", `g04-cast-text-only-${castId}.mp4`);
    const duration = await downloadAndVerifyMP4(request, videoUrl, mp4Path, testInfo);

    // Verify MP4 plays in a fresh browser tab
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

    // Minimum runtime assertion — a real render cannot finish in under 2 minutes
    assertMinRuntime(testStart);

    testInfo.annotations.push({
      type: "summary",
      description: `Text-only cast ${castId} rendered. Duration: ${duration}s. Elapsed: ${Math.round((Date.now() - testStart) / 1000)}s`,
    });
  });
});
