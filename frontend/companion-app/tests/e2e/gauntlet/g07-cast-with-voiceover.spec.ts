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
const CUSTOM_SCRIPT = "The quick brown fox jumps over the lazy dog near the riverbank at sunset.";

test.describe("G07 — Cast with Voiceover Block", () => {
  test.setTimeout(RENDER_TIMEOUT + 10 * 60 * 1000);

  test("voiceover cast: add voiceover block, custom script, render, play, verify text", async ({
    page,
    request,
  }, testInfo) => {
    const testStart = Date.now();
    await loginAsAdmin(page);

    // Create cast and reach script phase
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    const avatarCard = page.locator('[data-testid="avatar-card"]').first();
    await avatarCard.waitFor({ state: "visible", timeout: 30000 });
    await avatarCard.click();

    const castNameInput = page.locator('[data-testid="cast-name-input"]');
    await castNameInput.fill(`G07 Voiceover ${Date.now()}`);

    const generateBtn = page.locator('[data-testid="setup-generate-btn"]');
    await expect(generateBtn).toBeEnabled({ timeout: 5000 });
    await generateBtn.click();

    // Generate Script
    const generateScriptBtn = page.locator('button:has-text("Generate Script")');
    await expect(generateScriptBtn).toBeVisible({ timeout: 30000 });
    await generateScriptBtn.click();

    // Wait for blocks
    const scriptBlock = page.locator('text=Block 1');
    await scriptBlock.waitFor({ state: "visible", timeout: 120000 });

    // Add a Voiceover block — MUST find and click
    const addBlockBtn = page.locator(
      'button:has-text("Add block"), button:has-text("Add Block"), [data-testid="add-block-btn"]'
    ).first();
    await expect(addBlockBtn, "Add block button must be visible").toBeVisible({ timeout: 5000 });
    await addBlockBtn.click();
    await page.waitForTimeout(500);

    // Select Voiceover type — MUST find
    const voiceoverOption = page.locator(
      'button:has-text("Voiceover"), [data-testid="block-type-voiceover"], text=VOICEOVER'
    ).first();
    await expect(voiceoverOption, "Voiceover block type must be visible").toBeVisible({ timeout: 5000 });
    await voiceoverOption.click();
    await page.waitForTimeout(1000);

    // Find the last block's textarea and type custom text — MUST find
    const textareas = page.locator("textarea");
    const lastTextarea = textareas.last();
    await expect(lastTextarea, "Script textarea for voiceover block must be visible").toBeVisible({ timeout: 5000 });
    await lastTextarea.fill(CUSTOM_SCRIPT);
    await page.waitForTimeout(1000); // debounce save

    // Generate Audio
    const audioBtn = page.locator('button:has-text("Generate Audio")');
    await expect(audioBtn).toBeVisible({ timeout: 10000 });
    await audioBtn.click();

    // Wait for editor
    const editorShell = page.locator('[data-testid="editor-shell"]');
    await editorShell.waitFor({ state: "visible", timeout: 300000 });

    // Extract cast ID
    const url = page.url();
    const castIdMatch = url.match(/cast-builder\/([a-f0-9-]+)/);
    const castId = castIdMatch?.[1];
    expect(castId).toBeTruthy();

    // Verify custom script persists via API
    const castResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    const castApiData = await castResp.json();
    const allScripts = (castApiData.blocks || []).flatMap((b: any) =>
      (b.variants || []).map((v: any) => v.script_text)
    );
    expect(
      allScripts.some((s: string) => s?.includes("quick brown fox")),
      "Custom voiceover script must persist in cast data"
    ).toBe(true);

    // Finalize and render
    const castData = await finalizeAndWaitForRender(page, castId!, RENDER_TIMEOUT);
    const videoUrl = extractVideoUrl(castData);
    expect(videoUrl).toBeTruthy();

    // Play on Ready page
    await page.goto(`/cast-builder/${castId}`);
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    const readyVideo = page.locator('[data-testid="ready-video-player"]');
    await expect(readyVideo).toBeVisible({ timeout: 30000 });
    await clickPlayAndVerify(page, readyVideo, {
      clickOverlay: '[data-testid="ready-play-overlay"]',
    });

    // Download MP4 — REQUIRED
    const mp4Path = path.join(process.cwd(), "test-results", `g07-voiceover-${castId}.mp4`);
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
      description: `Voiceover cast ${castId} rendered with custom script. Duration: ${duration}s`,
    });
  });
});
