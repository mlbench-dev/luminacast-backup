import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { pollApiEndpoint } from "./helpers/polling";
import { API_BASE } from "./helpers/castFlow";

const SCOUT_TIMEOUT = 10 * 60 * 1000;

test.describe("G11 — Clone TikTok Scout", () => {
  test.setTimeout(15 * 60 * 1000);

  test("scout TikTok handle: scan videos, analyze full-body, select segment", async ({
    page,
  }, testInfo) => {
    await loginAsAdmin(page);

    // Navigate to clone page
    await page.goto("/my-avatar/clone");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    // ── Switch to Scout tab ──
    const scoutTab = page.locator(
      'button:has-text("Scout"), [data-testid="scout-tab"]'
    ).first();
    await expect(scoutTab, "Scout tab must be visible").toBeVisible({ timeout: 10000 });
    await scoutTab.click();
    await page.waitForTimeout(500);

    // ── Enter TikTok handle ──
    const handleInput = page.locator(
      'input[placeholder*="tiktok" i], input[placeholder*="handle" i], [data-testid="scout-handle-input"]'
    ).first();
    await expect(handleInput, "Handle input must be visible").toBeVisible({ timeout: 5000 });
    await handleInput.fill("charlidamelio");
    await page.waitForTimeout(300);

    // ── Start scan ──
    const scanBtn = page.locator(
      'button:has-text("Scan"), button:has-text("Scout"), [data-testid="scout-scan-btn"]'
    ).first();
    await expect(scanBtn, "Scan button must be visible").toBeVisible({ timeout: 5000 });
    await scanBtn.click();

    // ── Poll scan status via API ──
    // The frontend should show progress, but we verify via API
    const scanData = await pollApiEndpoint(
      page,
      `${API_BASE}/avatar/clone/scout`,
      (data: any) => {
        // Check if any scan completed (status == "complete")
        if (data?.status === "complete") return true;
        // May be returned as nested data
        if (data?.scan?.status === "complete") return true;
        return false;
      },
      { timeoutMs: SCOUT_TIMEOUT, intervalMs: 10000, message: "Clone scout scan complete" }
    );

    // ── Verify video grid appears ──
    const videoGrid = page.locator(
      '[data-testid="scout-video-grid"], .scout-video-grid'
    ).first();
    await expect(videoGrid, "Video grid must appear after scan").toBeVisible({ timeout: 30000 });

    // ── Verify at least one video card is shown ──
    const videoCards = page.locator(
      '[data-testid="scout-video-card"], .scout-video-card'
    );
    const videoCount = await videoCards.count();
    expect(videoCount, "Should have at least 1 scanned video").toBeGreaterThan(0);

    testInfo.annotations.push({
      type: "video-count",
      description: `${videoCount} TikTok videos found`,
    });

    // ── Check full-body detection results ──
    // Videos with full body should have a visual indicator (green border, badge, etc.)
    const fullBodyIndicators = page.locator(
      '[data-testid="full-body-badge"], .full-body-indicator, .border-green-500, [data-full-body="true"]'
    );
    const fullBodyCount = await fullBodyIndicators.count();
    testInfo.annotations.push({
      type: "full-body",
      description: `${fullBodyCount} videos with full-body detection`,
    });

    // ── Select a video (prefer full-body if available) ──
    const selectableVideo = fullBodyCount > 0
      ? fullBodyIndicators.first()
      : videoCards.first();
    await selectableVideo.click();
    await page.waitForTimeout(1000);

    // ── Verify segment picker or selection confirmation ──
    // After clicking a full-body video, should show segment picker or confirm dialog
    const segmentPicker = page.locator(
      '[data-testid="segment-picker"], .segment-picker-modal'
    );
    const selectBtn = page.locator(
      'button:has-text("Select"), button:has-text("Use this"), button:has-text("Create Clone")'
    ).first();

    const hasSegmentPicker = await segmentPicker.isVisible({ timeout: 5000 }).catch(() => false);
    const hasSelectBtn = await selectBtn.isVisible({ timeout: 5000 }).catch(() => false);

    expect(
      hasSegmentPicker || hasSelectBtn,
      "Should show segment picker or selection button after clicking video"
    ).toBe(true);

    if (hasSelectBtn) {
      await selectBtn.click();
      await page.waitForTimeout(2000);
    }

    testInfo.annotations.push({
      type: "summary",
      description: `Clone scout complete. ${videoCount} videos scanned, ${fullBodyCount} with full body. Segment selection UI verified.`,
    });
  });
});
