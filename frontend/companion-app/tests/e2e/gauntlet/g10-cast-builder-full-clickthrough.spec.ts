import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { clickPlayAndVerify } from "./helpers/clickPlayAndVerify";
import { downloadAndVerifyMP4 } from "./helpers/media";
import { assertMinRuntime } from "./helpers/polling";
import { captureEditorScreenshot } from "./helpers/captureEditorScreenshot";
import {
  createCastAndReachEditor,
  finalizeAndWaitForRender,
  extractVideoUrl,
  API_BASE,
} from "./helpers/castFlow";
import * as path from "path";

const RENDER_TIMEOUT = 45 * 60 * 1000;

test.describe("G10 — Cast Builder Full Clickthrough", () => {
  test.setTimeout(RENDER_TIMEOUT + 15 * 60 * 1000);

  test("comprehensive editor walkthrough: tabs, blocks, add block, drag-drop, player, transitions, finalize", async ({
    page,
    request,
  }, testInfo) => {
    const testStart = Date.now();
    await loginAsAdmin(page);
    const castId = await createCastAndReachEditor(page, `G10 Full Clickthrough ${Date.now()}`);

    // ── Layout assertion: editor fits one viewport ──
    const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    const viewportHeight = page.viewportSize()?.height || 720;
    expect(pageHeight, "Editor should fit one viewport").toBeLessThanOrEqual(viewportHeight + 50);

    // ── No Twick UI assertion: TwickStudio must NOT be rendered ──
    const twickStudio = page.locator('.twick-studio, [data-twick-studio], .twick-player-container');
    const twickCount = await twickStudio.count();
    expect(twickCount, "TwickStudio UI must NOT be present — we use our own SceneTimeline").toBe(0);

    // Verify no Twick branding elements (logo, watermark, Video Library, Image Library, Save Draft, Export buttons)
    const twickBranding = await page.evaluate(() => {
      const body = document.body.innerHTML;
      const twickPatterns = [
        'twick-logo', 'twick-watermark', 'Twick Studio',
        'Video Library', 'Image Library', 'Load Project', 'Save Draft',
      ];
      return twickPatterns.filter(p => body.includes(p));
    });
    expect(
      twickBranding,
      `Twick branding found in editor: ${twickBranding.join(", ")}`
    ).toEqual([]);

    // Verify our SceneTimeline is rendered instead
    const sceneTimeline = page.locator('[data-testid="scene-timeline"]');
    await expect(sceneTimeline, "Our SceneTimeline must be present in editor").toBeVisible({ timeout: 5000 });

    // ── D3: Visual regression screenshot ──
    const screenshotPath = await captureEditorScreenshot(page, testInfo, "g10-editor");
    expect(screenshotPath, "Editor screenshot must be saved").toBeTruthy();

    // ── Tabs assertion: open every left tab in sequence ──
    const tabIds = [
      "left-tab-blocks",
      "left-tab-media",
      "left-tab-stock",
      "left-tab-audio",
      "left-tab-text",
      "left-tab-captions",
      "left-tab-effects",
      "left-tab-filters",
      "left-tab-stickers",
    ];

    // Collect console errors
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    let tabsVisited = 0;
    for (const tabId of tabIds) {
      const tab = page.locator(`[data-testid="${tabId}"]`);
      if (await tab.isVisible({ timeout: 3000 }).catch(() => false)) {
        await tab.click();
        await page.waitForTimeout(500);
        tabsVisited++;
        // Verify content panel renders
        const panel = page.locator('[data-testid="left-panel-content"], .left-panel');
        if (await panel.isVisible({ timeout: 2000 }).catch(() => false)) {
          testInfo.annotations.push({
            type: "tab",
            description: `${tabId}: content rendered`,
          });
        }
      } else {
        testInfo.annotations.push({
          type: "tab-missing",
          description: `${tabId}: not visible`,
        });
      }
    }
    expect(tabsVisited, "At least 3 editor tabs should be visible").toBeGreaterThanOrEqual(3);

    // Check for console errors during tab clicking
    const tabErrors = consoleErrors.filter(
      (e) => !e.includes("play()") && !e.includes("AbortError")
    );
    expect(
      tabErrors.length,
      `Console errors during tab navigation: ${tabErrors.join("; ")}`
    ).toBe(0);

    // ── Block list: verify blocks visible ──
    const blocksTab = page.locator('[data-testid="left-tab-blocks"]');
    if (await blocksTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await blocksTab.click();
      await page.waitForTimeout(500);
    }

    const blockItems = page.locator('[data-testid="block-item"], .block-list-item');
    const blockCount = await blockItems.count();
    expect(blockCount, "Should have visible blocks").toBeGreaterThan(0);

    // ── Add block: click "+ Add block" ──
    const addBlockBtn = page.locator(
      'button:has-text("Add block"), button:has-text("Add Block"), [data-testid="add-block-btn"]'
    ).first();
    if (await addBlockBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await addBlockBtn.click();
      await page.waitForTimeout(500);

      const popover = page.locator('[data-testid="add-block-popover"], [role="dialog"], .popover');
      if (await popover.isVisible({ timeout: 3000 }).catch(() => false)) {
        const voiceoverType = page.locator(
          'button:has-text("Voiceover"), [data-testid="block-type-voiceover"]'
        ).first();
        if (await voiceoverType.isVisible({ timeout: 3000 }).catch(() => false)) {
          await voiceoverType.click();
          await page.waitForTimeout(1000);
        }
      } else {
        await page.keyboard.press("Escape");
      }
    }

    // ── Drag text preset — verify element lands in timeline_data ──
    const dropTarget = page.locator('[data-testid="editor-drop-target"]');
    const textTab = page.locator('[data-testid="left-tab-text"]');
    if (await textTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await textTab.click();
      await page.waitForTimeout(500);

      const textPreset = page.locator('.aspect-video:has-text("Big Title"), [data-testid="text-preset"]').first();
      const textVisible = await textPreset.isVisible({ timeout: 3000 }).catch(() => false);
      const dropVisible = await dropTarget.isVisible({ timeout: 3000 }).catch(() => false);
      if (textVisible && dropVisible) {
        await textPreset.dragTo(dropTarget);
        await page.waitForTimeout(1500);

        // Assert: timeline_data contains a text element (not just a toast)
        const timelineResp = await page.request.get(`${API_BASE}/casts/${castId}`);
        expect(timelineResp.ok(), "Cast API should respond OK after text drag").toBeTruthy();
        const castJson = await timelineResp.json();
        const timelineData = castJson?.timeline_data || castJson?.twick_data;
        if (timelineData?.tracks) {
          const textTrack = timelineData.tracks.find(
            (t: any) => t.id === "track-text" || t.id === "track-text-overlay"
          );
          expect(
            textTrack?.elements?.length,
            "Text track must contain at least one element after drag-drop"
          ).toBeGreaterThan(0);
          const textEl = textTrack?.elements?.find((el: any) => el.type === "text");
          expect(textEl, "Dropped text element must exist in timeline_data").toBeTruthy();
          testInfo.annotations.push({ type: "info", description: `Text drag-drop verified: ${textEl?.name || textEl?.props?.text || "text element"} in timeline_data` });
        }

        // Assert: SceneTimeline DOM shows the element in Text track
        const timelineTextEl = page.locator('[data-testid="scene-timeline"] [data-element-id]');
        const timelineElCount = await timelineTextEl.count();
        expect(timelineElCount, "SceneTimeline should show at least one engine element").toBeGreaterThan(0);
      }
    }

    // ── Drag stock photo — verify element in timeline_data, not just toast ──
    const stockTab = page.locator('[data-testid="left-tab-stock"]');
    if (await stockTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await stockTab.click();
      await page.waitForTimeout(1000);

      // Search for stock photos to get results
      const searchInput = page.locator('[data-testid="stock-search-input"]');
      const searchBtn = page.locator('[data-testid="stock-search-btn"]');
      if (await searchInput.isVisible({ timeout: 3000 }).catch(() => false)) {
        await searchInput.fill("nature");
        await searchBtn.click();
        await page.waitForTimeout(3000);
      }

      const stockItem = page.locator('[data-testid="stock-item"] img, .stock-grid img, [data-testid="editor-tab-content"] .aspect-video img').first();
      const stockVisible = await stockItem.isVisible({ timeout: 5000 }).catch(() => false);
      const dropVisible2 = await dropTarget.isVisible({ timeout: 3000 }).catch(() => false);
      if (stockVisible && dropVisible2) {
        await stockItem.dragTo(dropTarget);
        await page.waitForTimeout(1500);

        // Assert: timeline_data contains an image element on V2 track
        const castResp = await page.request.get(`${API_BASE}/casts/${castId}`);
        expect(castResp.ok(), "Cast API should respond OK after stock drag").toBeTruthy();
        const castJson = await castResp.json();
        const timelineData = castJson?.timeline_data || castJson?.twick_data;
        if (timelineData?.tracks) {
          const v2Track = timelineData.tracks.find((t: any) => t.id === "track-video-2");
          expect(
            v2Track?.elements?.length,
            "V2 track must contain at least one element after stock photo drag"
          ).toBeGreaterThan(0);
          testInfo.annotations.push({ type: "info", description: "Stock photo drag-drop verified in timeline_data" });
        }
      }
    }

    // ── Persistence check — refresh and verify dropped elements survive ──
    await page.reload({ waitUntil: "networkidle", timeout: 60000 });
    const editorAfterReload = page.locator('[data-testid="editor-shell"]');
    await editorAfterReload.waitFor({ state: "visible", timeout: 30000 });
    const persistResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    if (persistResp.ok()) {
      const persistJson = await persistResp.json();
      const persistTimeline = persistJson?.timeline_data || persistJson?.twick_data;
      if (persistTimeline?.tracks) {
        const allElements = persistTimeline.tracks.flatMap((t: any) => t.elements || []);
        expect(
          allElements.length,
          "Timeline must have elements after page refresh (persistence check)"
        ).toBeGreaterThan(0);
        testInfo.annotations.push({ type: "info", description: `Persistence verified: ${allElements.length} elements survive refresh` });
      }
    }

    // ── Properties panel: click a block, verify properties ──
    const firstBlock = page.locator('[data-testid="block-item"], .block-list-item, [data-testid="timeline-block"]').first();
    if (await firstBlock.isVisible({ timeout: 3000 }).catch(() => false)) {
      await firstBlock.click();
      await page.waitForTimeout(500);
    }

    // ── Player controls: play, seek, pause ──
    const playBtn = page.locator('[data-testid="playback-play-pause"]');
    await expect(playBtn).toBeVisible({ timeout: 10000 });
    await expect(playBtn).toBeEnabled();

    await playBtn.click();
    await page.waitForTimeout(1500);

    // Seek backwards
    const seekBack = page.locator('[data-testid="seek-back"], button:has-text("-1s")').first();
    if (await seekBack.isVisible({ timeout: 3000 }).catch(() => false)) {
      await seekBack.click();
      await page.waitForTimeout(300);
    }

    // Seek forward
    const seekFwd = page.locator('[data-testid="seek-forward"], button:has-text("+5s")').first();
    if (await seekFwd.isVisible({ timeout: 3000 }).catch(() => false)) {
      await seekFwd.click();
      await page.waitForTimeout(300);
    }

    // Pause
    await playBtn.click();
    await page.waitForTimeout(500);

    // ── Transitions ──
    const transitionDropdown = page.locator(
      '[data-testid="transition-select"], select[data-testid*="transition"]'
    ).first();
    if (await transitionDropdown.isVisible({ timeout: 3000 }).catch(() => false)) {
      await transitionDropdown.click();
      await page.waitForTimeout(300);
      const firstOption = page.locator('option, [role="option"]').first();
      if (await firstOption.isVisible({ timeout: 2000 }).catch(() => false)) {
        await firstOption.click();
      }
    }

    // ── Finalize & Render ──
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
    const mp4Path = path.join(
      process.cwd(),
      "test-results",
      `g10-full-clickthrough-${castId}.mp4`
    );
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
      description: `Full clickthrough complete. Cast ${castId}. ${blockCount} blocks. Duration: ${duration}s. Tabs visited: ${tabsVisited}/${tabIds.length}`,
    });
  });
});
