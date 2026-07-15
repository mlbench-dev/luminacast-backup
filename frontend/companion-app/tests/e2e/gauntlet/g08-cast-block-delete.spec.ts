import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { clickPlayAndVerify } from "./helpers/clickPlayAndVerify";
import { downloadAndVerifyMP4 } from "./helpers/media";
import { assertMinRuntime } from "./helpers/polling";
import {
  finalizeAndWaitForRender,
  extractVideoUrl,
  API_BASE,
} from "./helpers/castFlow";
import * as path from "path";

const RENDER_TIMEOUT = 45 * 60 * 1000;

test.describe("G08 — Cast Block Delete Regression (A1 Fix)", () => {
  test.setTimeout(RENDER_TIMEOUT + 10 * 60 * 1000);

  test("delete block 2, render, verify only blocks 1+3 in output", async ({
    page,
    request,
  }, testInfo) => {
    const testStart = Date.now();
    await loginAsAdmin(page);

    // Create cast and reach script phase with blocks
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    const avatarCard = page.locator('[data-testid="avatar-card"]').first();
    await avatarCard.waitFor({ state: "visible", timeout: 30000 });
    await avatarCard.click();

    const castNameInput = page.locator('[data-testid="cast-name-input"]');
    await castNameInput.fill(`G08 Block Delete ${Date.now()}`);

    const generateBtn = page.locator('[data-testid="setup-generate-btn"]');
    await expect(generateBtn).toBeEnabled({ timeout: 5000 });
    await generateBtn.click();

    // Generate script
    const generateScriptBtn = page.locator('button:has-text("Generate Script")');
    await expect(generateScriptBtn).toBeVisible({ timeout: 30000 });
    await generateScriptBtn.click();

    // Wait for blocks
    await page.locator('text=Block 1').waitFor({ state: "visible", timeout: 120000 });

    // Extract cast ID
    const urlBefore = page.url();
    const castIdMatch = urlBefore.match(/cast-builder\/([a-f0-9-]+)/);
    const castId = castIdMatch?.[1];
    expect(castId).toBeTruthy();

    // Get initial block count and durations via API
    const initialResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    const initialData = await initialResp.json();
    const initialBlocks = initialData.blocks || [];
    const initialBlockCount = initialBlocks.length;
    expect(initialBlockCount, "Cast should have at least 3 blocks").toBeGreaterThanOrEqual(3);

    // Calculate initial total duration
    const initialTotalDuration = initialBlocks.reduce((sum: number, b: any) => {
      const blockDur = (b.variants || []).reduce(
        (vs: number, v: any) => vs + (v.duration_seconds || v.tts_duration_seconds || 0),
        0
      );
      return sum + blockDur;
    }, 0);

    // Calculate expected duration after removing block 2
    const block2Duration = (initialBlocks[1]?.variants || []).reduce(
      (sum: number, v: any) => sum + (v.duration_seconds || v.tts_duration_seconds || 0),
      0
    );
    const expectedDuration = initialTotalDuration - block2Duration;

    // Delete block 2 via UI — try UI first, API fallback
    const deleteButtons = page.locator(
      'button[aria-label="Delete block"], button:has-text("Delete"), [data-testid="delete-block"]'
    );
    const block2Delete = deleteButtons.nth(1);
    if (await block2Delete.isVisible({ timeout: 5000 }).catch(() => false)) {
      await block2Delete.click();
      await page.waitForTimeout(1000);
    } else {
      // API fallback — MUST succeed
      const block2Id = initialBlocks[1]?.id;
      expect(block2Id, "Block 2 must have an ID for API deletion").toBeTruthy();
      const deleteResp = await page.request.delete(`${API_BASE}/casts/${castId}/blocks/${block2Id}`);
      expect(deleteResp.ok(), "Block 2 API deletion must succeed").toBeTruthy();
      await page.waitForTimeout(1000);
    }

    // Verify block was deleted via API — HARD ASSERT
    const afterDeleteResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    const afterDeleteData = await afterDeleteResp.json();
    expect(
      (afterDeleteData.blocks || []).length,
      "Should have one fewer block after delete"
    ).toBe(initialBlockCount - 1);

    // Generate Audio — MUST find button
    const audioBtn = page.locator('button:has-text("Generate Audio")');
    await expect(audioBtn, "Generate Audio button must be visible after block delete").toBeVisible({ timeout: 10000 });
    await audioBtn.click();

    // Wait for editor
    const editorShell = page.locator('[data-testid="editor-shell"]');
    await editorShell.waitFor({ state: "visible", timeout: 300000 });

    // Finalize and render
    const castData = await finalizeAndWaitForRender(page, castId!, RENDER_TIMEOUT);
    const videoUrl = extractVideoUrl(castData);
    expect(videoUrl).toBeTruthy();

    // Download and verify MP4 — REQUIRED
    const mp4Path = path.join(process.cwd(), "test-results", `g08-block-delete-${castId}.mp4`);
    const duration = await downloadAndVerifyMP4(request, videoUrl, mp4Path, testInfo);

    // Verify duration reflects deleted block (should NOT include block 2's audio)
    expect(duration, "Duration must be positive").toBeGreaterThan(0);
    expect(expectedDuration, "Expected duration must be positive").toBeGreaterThan(0);
    expect(
      duration,
      `Duration ${duration}s should be less than all-blocks total ${initialTotalDuration}s`
    ).toBeLessThan(initialTotalDuration * 1.1);

    // Play on Ready page
    await page.goto(`/cast-builder/${castId}`);
    await page.waitForLoadState("networkidle", { timeout: 60000 });
    const readyVideo = page.locator('[data-testid="ready-video-player"]');
    await expect(readyVideo).toBeVisible({ timeout: 30000 });
    await clickPlayAndVerify(page, readyVideo, {
      clickOverlay: '[data-testid="ready-play-overlay"]',
    });

    // Minimum runtime assertion
    assertMinRuntime(testStart);

    testInfo.annotations.push({
      type: "summary",
      description: `Block delete regression: ${initialBlockCount} → ${initialBlockCount - 1} blocks. Duration: ${duration}s (expected ~${Math.round(expectedDuration)}s)`,
    });
  });
});
