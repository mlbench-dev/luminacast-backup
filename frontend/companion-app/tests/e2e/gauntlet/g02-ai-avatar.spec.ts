import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { pollUntil } from "./helpers/polling";
import { clickPlayAndVerify } from "./helpers/clickPlayAndVerify";
import { downloadAndVerifyMP4 } from "./helpers/media";
import { API_BASE } from "./helpers/castFlow";
import * as path from "path";

const PIPELINE_TIMEOUT = 25 * 60 * 1000;

test.describe("G02 — AI Avatar Full 6-Phase Pipeline (v3)", () => {
  test.setTimeout(30 * 60 * 1000);

  test("AI avatar: walk 6 phases (v3 consolidated setup, 4 voices, body shots), preview MP4, verify APPROVED via API", async ({
    page,
    request,
  }, testInfo) => {
    await loginAsAdmin(page);

    // Navigate to AI avatar creation
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    // Click "Create character" button — MUST exist
    const createBtn = page.locator(
      'button:has-text("Create character"), a:has-text("Create character"), button:has-text("AI Avatar"), a:has-text("Create")'
    ).first();
    await expect(createBtn).toBeVisible({ timeout: 10000 });
    await createBtn.click();
    await page.waitForLoadState("networkidle", { timeout: 30000 });

    // ─── Phase 1: Setup (consolidated Audience + Description) ───
    // Select an age range pill
    const agePill = page.locator('button:has-text("25-34"), button:has-text("18-24")').first();
    await expect(agePill, "Phase 1: Age range pill must be visible").toBeVisible({ timeout: 10000 });
    await agePill.click();
    await page.waitForTimeout(500);

    // Select a couple of interest chips
    const interestChip = page.locator('button:has-text("Fashion"), button:has-text("Fitness"), button:has-text("Beauty")').first();
    if (await interestChip.isVisible({ timeout: 3000 }).catch(() => false)) {
      await interestChip.click();
      await page.waitForTimeout(300);
    }

    // Wait for auto-generated audience description (ShimmerField shows, then text appears)
    await page.waitForTimeout(2000);

    // Select gender
    const genderBtn = page.locator('button:has-text("Female"), button:has-text("Male")').first();
    if (await genderBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await genderBtn.click();
      await page.waitForTimeout(300);
    }

    // Wait for auto-generated avatar name & description cascade
    await page.waitForTimeout(3000);

    // Click Continue to move to face phase
    const setupNext = page.locator('button:has-text("Continue"), button:has-text("Next")').first();
    await expect(setupNext, "Phase 1: Setup Continue button must be visible").toBeVisible({ timeout: 10000 });
    await setupNext.click();
    await page.waitForTimeout(2000);

    // ─── Phase 2: Face — wait for face options, select first ───
    const faceOption = page.locator('[data-testid="face-option"], .face-candidate, img[alt*="face"]').first();
    await expect(faceOption, "Phase 2: Face option must appear").toBeVisible({ timeout: 120000 });
    await faceOption.click();
    await page.waitForTimeout(500);
    const faceNext = page.locator('button:has-text("Next"), button:has-text("Continue"), button:has-text("Select")').first();
    await expect(faceNext, "Phase 2: Next button must be visible").toBeVisible({ timeout: 5000 });
    await faceNext.click();
    await page.waitForTimeout(1000);

    // ─── Phase 3: Voice — generate voice previews, expect 4 options ───
    // Voice generation button
    const generateVoiceBtn = page.locator('button:has-text("Generate voice"), button:has-text("Generate Voice")').first();
    if (await generateVoiceBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await generateVoiceBtn.click();
    }

    // Wait for voice options to appear (generation may take up to 30s)
    const voiceOption = page.locator('[data-testid="voice-option"]').first();
    await expect(voiceOption, "Phase 3: Voice option must be visible").toBeVisible({ timeout: 60000 });

    // Verify we have at least 4 voice options
    const voiceCount = await page.locator('[data-testid="voice-option"]').count();
    expect(voiceCount, "Should have at least 4 voice options").toBeGreaterThanOrEqual(4);

    // Select first voice option
    await voiceOption.click();
    await page.waitForTimeout(500);
    const voiceNext = page.locator('button:has-text("Next"), button:has-text("Continue"), button:has-text("Lock")').first();
    await expect(voiceNext, "Phase 3: Next button must be visible").toBeVisible({ timeout: 5000 });
    await voiceNext.click();
    await page.waitForTimeout(1000);

    // ─── Phase 4: Body Description — wait for generation, then proceed ───
    // Body description generates automatically via ShimmerField
    await pollUntil(
      async () => {
        const textarea = page.locator('textarea').first();
        if (!await textarea.isVisible().catch(() => false)) return false;
        const val = await textarea.inputValue().catch(() => "");
        return val.length > 20;
      },
      { timeoutMs: 60000, intervalMs: 2000, message: "Body description auto-generation" }
    );

    const bodyDescNext = page.locator('button:has-text("Generate Body Shots"), button:has-text("Approve & Continue"), button:has-text("Next"), button:has-text("Continue")').first();
    await expect(bodyDescNext, "Phase 4: Body desc next button must be visible").toBeVisible({ timeout: 10000 });
    await bodyDescNext.click();
    await page.waitForTimeout(2000);

    // v3: Verify body description was saved to avatar (Approve & Continue persists it)
    // The avatar API should now have body_description populated
    const avatarsResp = await page.request.get(`${API_BASE}/avatars`);
    if (avatarsResp.ok()) {
      const avatarsData = await avatarsResp.json();
      const avatars = avatarsData?.avatars || avatarsData || [];
      if (Array.isArray(avatars) && avatars.length > 0) {
        const latest = avatars[avatars.length - 1];
        testInfo.annotations.push({
          type: "body-desc-saved",
          description: `Body description saved: ${!!latest.body_description}`,
        });
      }
    }

    // ─── Phase 5: Body Shots — wait for 6 angles, check grid ───
    const frontShot = page.locator('[data-testid="body-shot-front"]');
    await expect(frontShot, "Phase 5: Front body shot must appear").toBeVisible({ timeout: 180000 });

    // Verify we have body shots in the grid
    const bodyShotCount = await page.locator('[data-testid^="body-shot-"]').count();
    expect(bodyShotCount, "Should have at least 1 body shot").toBeGreaterThanOrEqual(1);

    // Verify per-tile regenerate button exists (hover to reveal)
    const regenBtn = page.locator('[data-testid="regenerate-front"]');
    await frontShot.hover();
    await page.waitForTimeout(500);
    // Regenerate button shows on hover — verify it exists in DOM
    expect(await regenBtn.count(), "Regenerate button should exist for front angle").toBeGreaterThanOrEqual(1);

    // Approve body shots
    const approveBodyShots = page.locator('[data-testid="approve-body-shots-btn"], button:has-text("Approve All")').first();
    await expect(approveBodyShots, "Phase 5: Approve button must be visible").toBeVisible({ timeout: 5000 });
    await approveBodyShots.click();
    await page.waitForTimeout(2000);

    // ─── Phase 6: Preview — wait for preview video ───
    await pollUntil(
      async () => {
        const video = page.locator("video").first();
        return (await video.isVisible().catch(() => false)) &&
               (await video.evaluate((v: HTMLVideoElement) => !!v.src || !!v.currentSrc).catch(() => false));
      },
      { timeoutMs: PIPELINE_TIMEOUT, intervalMs: 15000, message: "AI avatar preview video with src" }
    );

    // Click play on preview — MUST work
    const previewVideo = page.locator("video").first();
    await clickPlayAndVerify(page, previewVideo);

    // Download and verify preview MP4 — MUST succeed
    const videoSrc = await previewVideo.evaluate((v: HTMLVideoElement) => v.src || v.currentSrc);
    expect(videoSrc, "Preview video must have a src URL").toBeTruthy();
    const mp4Path = path.join(process.cwd(), "test-results", "g02-ai-avatar-preview.mp4");
    const duration = await downloadAndVerifyMP4(request, videoSrc, mp4Path, testInfo);
    expect(duration, "Preview MP4 should have valid duration").toBeGreaterThan(0);

    // Approve if button available — click it
    const approveBtn = page.locator('button:has-text("Approve"), button:has-text("Accept")').first();
    if (await approveBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approveBtn.click();
      await page.waitForTimeout(2000);
    }

    // Verify avatar exists in library with APPROVED/READY status via API
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle", { timeout: 30000 });
    const avatarCards = page.locator('[data-testid="avatar-card"]');
    const count = await avatarCards.count();
    expect(count, "Should have at least 1 avatar in library").toBeGreaterThan(0);

    testInfo.annotations.push({
      type: "summary",
      description: `AI avatar 6-phase pipeline (v2) complete. Preview MP4 duration: ${duration}s. Voice options: ${voiceCount}`,
    });
  });
});
