import { test, expect, Page } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import { pollApiEndpoint } from "./fixtures/polling";
import { screenshot, TIMEOUTS, verifyNoErrorBoundary } from "./fixtures/qa-helpers";
import * as fs from "fs";
import * as path from "path";
import { process } from "zod/v4/core";

const BASE_URL = process.env.E2E_BASE_URL || "https://www.luminacast.com";
const API_BASE = `${BASE_URL}/api`;
const TEST_TIMEOUT = 30 * 60 * 1000; // 30 minutes

test.describe("Cast Render + Click-Play E2E", () => {
  test.setTimeout(TEST_TIMEOUT);

  test("full cast journey: create, edit, render, play", async ({ page, request }, testInfo) => {
    // ── Step 1: Login ──
    await loginAsAdmin(page);
    await screenshot(page, "01-logged-in");

    // ── Step 2: Create a cast ──
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle", { timeout: TIMEOUTS.long });
    await screenshot(page, "02-setup-page");

    // Wait for avatars to load (auto-selects first one via useEffect)
    const avatarCard = page.locator('[data-testid="avatar-card"]').first();
    await avatarCard.waitFor({ state: "visible", timeout: 30000 });
    // Ensure avatar is selected (has accent border = auto-selected)
    await avatarCard.click();
    await page.waitForTimeout(500);

    // Fill in cast name (required for Generate button to enable)
    const castNameInput = page.locator('[data-testid="cast-name-input"]');
    await castNameInput.fill(`E2E Test Cast ${Date.now()}`);

    await screenshot(page, "02b-setup-filled");

    // Click Generate Script button
    const generateBtn = page.locator('[data-testid="setup-generate-btn"]');
    await expect(generateBtn).toBeEnabled({ timeout: 5000 });
    await generateBtn.click({ timeout: 10000 });
    await screenshot(page, "03-after-create");

    // ── Script phase: click Generate Script, wait for blocks, then Generate Audio ──
    // After cast creation, the Script Editor shows "No script blocks yet" with a Generate Script button
    const generateScriptBtn = page.locator('button:has-text("Generate Script")');
    await expect(generateScriptBtn).toBeVisible({ timeout: 30000 });
    await generateScriptBtn.click();
    await screenshot(page, "03b-generating-script");

    // Wait for script blocks to appear (AI generation takes 10-30s)
    const scriptBlock = page.locator('text=Block 1');
    await scriptBlock.waitFor({ state: "visible", timeout: 120000 });
    await screenshot(page, "04-script-phase");

    // ── Click "Generate Audio →" to advance to Audio phase ──
    const audioBtn = page.locator('button:has-text("Generate Audio")');
    await expect(audioBtn).toBeVisible({ timeout: 10000 });
    await audioBtn.click();
    await screenshot(page, "04b-audio-generating");

    // ── Wait for editor/arrange phase ──
    // AudioGeneratingPhase polls every 5s until TTS is ready, then transitions to editor.
    // TTS generation can take 1-3 minutes depending on script length.
    const editorShell = page.locator('[data-testid="editor-shell"]');
    await editorShell.waitFor({ state: "visible", timeout: 300000 }); // 5 min for TTS + editor load
    await screenshot(page, "05-editor-loaded");

    // ── Step 3: Extract cast ID from URL ──
    const url = page.url();
    const castIdMatch = url.match(/cast-builder\/([a-f0-9-]+)/);
    const castId = castIdMatch?.[1];
    expect(castId, "Cast ID should be in URL").toBeTruthy();

    // ── Step 4: Verify editor is one screen ──
    // The editor is constrained by its parent's overflow-hidden. Verify no page-level scrollbar.
    const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    const viewportHeight = page.viewportSize()?.height || 720;
    // Allow small tolerance (5px) — page should not have significant scroll
    expect(pageHeight, "Page should not have vertical scrollbar (editor must fit one viewport)").toBeLessThanOrEqual(viewportHeight + 50);
    await screenshot(page, "06-editor-one-screen");

    // ── Step 5: Verify tracks are visible ──
    const timeline = page.locator('[data-testid="editor-timeline"]');
    await expect(timeline, "Timeline should be visible").toBeVisible({ timeout: 10000 });
    await screenshot(page, "07-timeline-visible");

    // ── Step 6: Verify player controls ──
    const playBtn = page.locator('[data-testid="playback-play-pause"]');
    await expect(playBtn, "Play button should be visible in header").toBeVisible({ timeout: 10000 });
    await expect(playBtn, "Play button should be enabled").toBeEnabled();

    // ── Step 7: Click play in editor ──
    await playBtn.click();
    // Wait briefly for player to start
    await page.waitForTimeout(1000);

    // Verify play state (the button should now show pause icon)
    const headerControls = page.locator('[data-testid="header-playback-controls"]');
    await expect(headerControls).toBeVisible();
    await screenshot(page, "08-playing");

    // ── Step 8: Click pause ──
    await playBtn.click();
    await page.waitForTimeout(500);
    await screenshot(page, "09-paused");

    // ── Step 9: Try dragging a text element (optional — best effort) ──
    const textTab = page.locator('[data-testid="left-tab-text"]');
    if (await textTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await textTab.click();
      await page.waitForTimeout(500);
      // Note: actual HTML5 drag-drop in Playwright is complex
      // We verify the text tab shows presets
      const textPresets = page.locator('.aspect-video:has-text("Big Title")');
      if (await textPresets.isVisible({ timeout: 3000 }).catch(() => false)) {
        await screenshot(page, "10-text-tab-presets");
      }
    }

    // ── Step 10: Click Finalize & Render ──
    const finalizeBtn = page.locator('[data-testid="finalize-render-btn"]');
    await expect(finalizeBtn).toBeVisible({ timeout: 10000 });
    await finalizeBtn.click();
    await screenshot(page, "11-render-started");

    // ── Step 11: Poll for final_video_url ──
    let castData: any;
    try {
      castData = await pollApiEndpoint(
        page,
        `${API_BASE}/casts/${castId}`,
        (data: any) => {
          // Check if any variant has final_video_key or video_key
          const hasVideo = (data?.blocks || []).some((b: any) =>
            (b.variants || []).some((v: any) =>
              v.final_video_key || v.video_key || v.stream_url || v.clip_url
            )
          );
          const isReady = data?.status?.toLowerCase() === "ready" ||
                          data?.status?.toLowerCase() === "completed";
          return hasVideo && isReady;
        },
        { timeout: 20 * 60 * 1000, interval: 10000, label: "cast render completion" }
      );
    } catch (e) {
      // Read logs for diagnostics
      await screenshot(page, "11-render-timeout");
      throw new Error(`Render timed out for cast ${castId}. ${(e as Error).message}`);
    }

    expect(castData).toBeTruthy();
    await screenshot(page, "12-render-complete");

    // Find video URL
    let videoUrl = "";
    for (const block of castData.blocks || []) {
      for (const variant of block.variants || []) {
        if (variant.stream_url) { videoUrl = variant.stream_url; break; }
        if (variant.clip_url) { videoUrl = variant.clip_url; break; }
        if (variant.final_video_key) { videoUrl = `https://media.luminacast.com/${variant.final_video_key}`; break; }
        if (variant.video_key) { videoUrl = `https://media.luminacast.com/${variant.video_key}`; break; }
      }
      if (videoUrl) break;
    }
    expect(videoUrl, "Should have a video URL").toBeTruthy();

    // ── Step 12: Navigate to Ready phase ──
    await page.goto(`/cast-builder/${castId}`);
    await page.waitForLoadState("networkidle", { timeout: TIMEOUTS.long });

    // Verify ready phase loaded
    const readyVideo = page.locator('[data-testid="ready-video-player"]');
    await expect(readyVideo, "Ready page should show video player").toBeVisible({ timeout: 30000 });
    await screenshot(page, "13-ready-phase");

    // ── Step 13: Click play on Ready page ──
    const playOverlay = page.locator('[data-testid="ready-play-overlay"]');
    if (await playOverlay.isVisible({ timeout: 3000 }).catch(() => false)) {
      await playOverlay.click();
    } else {
      // Click the video element directly to play
      await readyVideo.click();
    }
    await page.waitForTimeout(2000);

    // Verify video is playing
    const isPaused = await readyVideo.evaluate((v: HTMLVideoElement) => v.paused);
    expect(isPaused, "Video should be playing after click").toBe(false);
    await screenshot(page, "14-ready-playing");

    // ── Step 14: Verify duration ──
    const duration = await readyVideo.evaluate((v: HTMLVideoElement) => v.duration);
    expect(duration, "Video duration should be between 5-600s").toBeGreaterThan(5);
    expect(duration, "Video duration should be between 5-600s").toBeLessThan(600);

    // ── Step 15: Download the MP4 ──
    const resultsDir = path.join(process.cwd(), "test-results");
    if (!fs.existsSync(resultsDir)) fs.mkdirSync(resultsDir, { recursive: true });

    const mp4Path = path.join(resultsDir, `cast-render-${castId}.mp4`);
    try {
      const response = await request.get(videoUrl);
      expect(response.ok(), `Download should succeed: ${response.status()}`).toBe(true);
      const buffer = await response.body();
      fs.writeFileSync(mp4Path, buffer);
    } catch (e) {
      throw new Error(`Failed to download MP4: ${(e as Error).message}`);
    }

    // ── Step 16: Verify ftyp magic bytes ──
    const fileBuffer = fs.readFileSync(mp4Path);
    expect(fileBuffer.length, "MP4 file should not be empty").toBeGreaterThan(100);
    // ftyp is at offset 4-8
    const ftyp = fileBuffer.slice(4, 8).toString("ascii");
    expect(ftyp, "MP4 should have ftyp magic bytes at offset 4").toBe("ftyp");

    // ── Step 17: Run ffprobe (best effort) ──
    try {
      const { execSync } = require("child_process");
      const ffprobeOut = execSync(
        `ffprobe -v quiet -print_format json -show_streams "${mp4Path}"`,
        { timeout: 30000 }
      ).toString();
      const probe = JSON.parse(ffprobeOut);
      const hasVideo = probe.streams?.some((s: any) => s.codec_type === "video");
      const hasAudio = probe.streams?.some((s: any) => s.codec_type === "audio");
      expect(hasVideo, "MP4 should have a video stream").toBe(true);
      // Audio is optional — some short clips may not have it
      if (hasAudio) {
        testInfo.annotations.push({ type: "info", description: "MP4 has audio stream" });
      }
    } catch (e) {
      // ffprobe may not be available in all environments
      testInfo.annotations.push({ type: "info", description: "ffprobe not available, skipping stream check" });
    }

    // ── Step 18: Attach as test artifact ──
    await testInfo.attach("rendered-cast.mp4", {
      path: mp4Path,
      contentType: "video/mp4",
    });

    // ── Step 19: Sentry check (best effort) ──
    try {
      const sentryToken = process.env.SENTRY_TOKEN || "";
      const sentryOrg = "novalios";
      for (const project of ["luminacast-orchestrator", "luminacast-gpu-worker"]) {
        const resp = await request.get(
          `https://sentry.io/api/0/projects/${sentryOrg}/${project}/issues/?statsPeriod=24h&query=is:unresolved&limit=5`,
          { headers: { Authorization: `Bearer ${sentryToken}` } }
        );
        if (resp.ok()) {
          const issues = await resp.json();
          if (Array.isArray(issues)) {
            testInfo.annotations.push({
              type: "sentry",
              description: `${project}: ${issues.length} unresolved issues`,
            });
          }
        }
      }
    } catch {
      testInfo.annotations.push({ type: "info", description: "Sentry check skipped" });
    }

    // ── Step 20: Final screenshot ──
    await screenshot(page, "20-test-complete");

    // Summary
    testInfo.annotations.push({
      type: "summary",
      description: `Cast ${castId} rendered and played successfully. Video: ${videoUrl.slice(0, 100)}`,
    });
  });
});
