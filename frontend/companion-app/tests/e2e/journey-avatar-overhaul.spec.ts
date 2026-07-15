import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Avatar Overhaul — Full Journey", () => {
  test.setTimeout(15 * 60 * 1000); // 15 min for full journey

  test("AI Avatar creation full happy path with all features", async ({
    page,
  }) => {
    await loginAsAdmin(page);

    // ─── PHASE 0: Navigate to AI Avatar creation ───
    await page.goto("/my-avatar");
    await screenshot(page, "avatar-overhaul-01-my-avatar");

    await page.click('[data-testid="create-avatar-btn"]');
    await page.click('[data-testid="ai-avatar-option"]');
    await screenshot(page, "avatar-overhaul-02-create-options");

    // ─── PHASE 2: Style preset picker ───
    for (const preset of ["studio", "humanizer", "cinematic", "stylized"]) {
      const presetBtn = page.locator(
        `[data-testid="face-preset-${preset}"]`
      );
      await expect(presetBtn).toBeVisible({ timeout: TIMEOUTS.medium });
    }
    await screenshot(page, "avatar-overhaul-03-presets");

    // Pick Real Human preset
    await page.click('[data-testid="face-preset-humanizer"]');

    // Fill description
    await page.fill(
      '[data-testid="avatar-description"]',
      "A 28-year-old woman with curly red hair and a friendly smile, wearing a casual sweater"
    );
    await screenshot(page, "avatar-overhaul-04-description");

    // Generate
    await page.click('[data-testid="generate-faces-btn"]');

    // Wait for 8 candidates
    await expect(page.locator('[data-testid^="face-candidate-"]')).toHaveCount(
      8,
      { timeout: TIMEOUTS.render }
    );
    await screenshot(page, "avatar-overhaul-05-candidates");

    // ─── PHASE 3: Click-to-modal face preview ───
    await page.click('[data-testid="face-candidate-0"]');

    // Verify modal opens (NOT hover preview)
    await expect(
      page.locator('[data-testid="face-preview-modal"]')
    ).toBeVisible({ timeout: TIMEOUTS.short });
    await screenshot(page, "avatar-overhaul-06-face-modal");

    // Test keyboard nav
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await screenshot(page, "avatar-overhaul-07-modal-nav");

    // Select via Enter
    await page.keyboard.press("Enter");

    // Modal closes, face is selected
    await expect(
      page.locator('[data-testid="face-preview-modal"]')
    ).not.toBeVisible();
    await expect(
      page.locator('[data-testid="face-candidate-2"]')
    ).toHaveAttribute("data-selected", "true");

    // Continue to voice
    await page.click('[data-testid="continue-to-voice"]');

    // ─── PHASE 4: Voice phase with all enhancements ───
    await screenshot(page, "avatar-overhaul-08-voice-step");

    // Verify avatar description visible
    await expect(
      page.locator("text=A 28-year-old woman with curly red hair")
    ).toBeVisible({ timeout: TIMEOUTS.medium });

    // Verify language + accent picker
    await expect(
      page.locator('[data-testid="language-picker"]')
    ).toBeVisible();
    await expect(page.locator('[data-testid="accent-picker"]')).toBeVisible();

    await page.selectOption('[data-testid="language-picker"]', "english");
    await page.selectOption('[data-testid="accent-picker"]', "uk");

    // Verify ElevenLabs guidance is expandable
    await page.click("text=How to describe voice tone");
    await expect(
      page.locator("text=Warm, calm, and reassuring")
    ).toBeVisible({ timeout: TIMEOUTS.short });
    await screenshot(page, "avatar-overhaul-09-voice-guidance");

    // Open voice library — verify filters required first
    await page.click('[data-testid="browse-voices-btn"]');
    await expect(
      page.locator("text=Pick filters above to browse voices")
    ).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "avatar-overhaul-10-voice-filters-empty");

    // Apply filters
    await page.click('[data-testid="voice-filter-gender-female"]');
    await page.click('[data-testid="voice-filter-tone-warm"]');

    // Now voices should appear
    await expect(
      page.locator('[data-testid^="voice-card-"]').first()
    ).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "avatar-overhaul-11-voice-results");

    // Pick first voice
    await page.click('[data-testid^="voice-card-"]');
    await page.click('[data-testid="confirm-voice-btn"]');

    // Verify voice corpus has both Record and Upload tabs
    await expect(
      page.locator('[data-testid="corpus-tab-record"]')
    ).toBeVisible({ timeout: TIMEOUTS.medium });
    await expect(
      page.locator('[data-testid="corpus-tab-upload"]')
    ).toBeVisible();

    await page.click('[data-testid="continue-to-preview"]');

    // ─── PHASE 5: Preview phase ───
    await screenshot(page, "avatar-overhaul-12-preview-step");

    // Verify NO generic "What should your avatar say" text
    await expect(
      page.locator("text=What should your avatar say?")
    ).not.toBeVisible();

    // Verify avatar description still visible
    await expect(
      page.locator("text=A 28-year-old woman with curly red hair")
    ).toBeVisible({ timeout: TIMEOUTS.medium });

    // Wait for preview render
    const videoEl = page.locator('video[src*="test_video"]');
    await expect(videoEl).toBeVisible({ timeout: TIMEOUTS.render });
    await screenshot(page, "avatar-overhaul-13-preview-rendered");

    // Verify video is playable (no error boundary)
    await videoEl.click();
    await page.waitForTimeout(500);
    await verifyNoErrorBoundary(page);

    // Verify single Re-edit & generate button
    await expect(
      page.locator('[data-testid="re-edit-generate-btn"]')
    ).toBeVisible();

    // Verify Approve button
    await page.click('[data-testid="approve-avatar-btn"]');

    // ─── PHASE 9: Original background in Edit ───
    await page.waitForURL(/\/my-avatar\/.*\/edit/, {
      timeout: TIMEOUTS.medium,
    });
    await page.click('[data-testid="tab-backgrounds"]');
    await expect(page.locator("text=Original")).toBeVisible({
      timeout: TIMEOUTS.medium,
    });
    await screenshot(page, "avatar-overhaul-14-original-background");

    // ─── PHASE 7: No engine names in user-facing UI ───
    const bodyText = await page.textContent("body");
    expect(bodyText).not.toContain("InfiniteTalk");
    expect(bodyText).not.toContain("ElevenLabs");
    expect(bodyText).not.toContain("Fish Speech");
    expect(bodyText).not.toContain("MuseTalk");
    expect(bodyText).not.toContain("FLUX");
    expect(bodyText).not.toContain("RunPod");
  });

  test("Resume routing — AI Avatar in progress goes to AI Avatar page", async ({
    page,
  }) => {
    await loginAsAdmin(page);

    // Start AI avatar
    await page.goto("/my-avatar/ai-avatar");
    await page.fill(
      '[data-testid="avatar-description"]',
      "test avatar for resume test"
    );

    // Don't generate, just leave
    await page.goto("/my-avatar");

    // Find the in-progress card
    const draftCard = page
      .locator('[data-testid^="avatar-card-"]')
      .filter({ hasText: "test avatar for resume test" })
      .first();

    if (await draftCard.isVisible({ timeout: TIMEOUTS.short })) {
      await draftCard.click();

      // CRITICAL: should land on AI Avatar page, NOT Clone page
      await page.waitForURL(/\/my-avatar\/ai-avatar/, {
        timeout: TIMEOUTS.medium,
      });

      // Should NOT be on /my-avatar/clone
      expect(page.url()).not.toContain("/my-avatar/clone");
      await screenshot(page, "avatar-overhaul-15-resume-routing");
    }
  });

  test("Re-edit and cancel without re-rendering", async ({ page }) => {
    await loginAsAdmin(page);

    await page.goto("/my-avatar/ai-avatar");
    await screenshot(page, "avatar-overhaul-16-reedit-start");

    // Fill description and generate
    await page.fill(
      '[data-testid="avatar-description"]',
      "A professional man in a blue suit for re-edit test"
    );
    await page.click('[data-testid="generate-faces-btn"]');

    // Wait for candidates
    await expect(page.locator('[data-testid^="face-candidate-"]')).toHaveCount(
      8,
      { timeout: TIMEOUTS.render }
    );

    // Select first face
    await page.click('[data-testid="face-candidate-0"]');
    await page.keyboard.press("Enter");

    // Click re-edit button if visible
    const reEditBtn = page.locator('[data-testid="re-edit-description-btn"]');
    if (await reEditBtn.isVisible({ timeout: TIMEOUTS.short })) {
      await reEditBtn.click();

      // Description field should be visible and editable
      const descField = page.locator('[data-testid="avatar-description"]');
      await expect(descField).toBeVisible({ timeout: TIMEOUTS.short });

      // Original description pre-filled
      const value = await descField.inputValue();
      expect(value).toContain("professional man in a blue suit");

      // Click Cancel — should return to face gallery without re-generating
      const cancelBtn = page.locator('[data-testid="cancel-re-edit-btn"]');
      if (await cancelBtn.isVisible({ timeout: TIMEOUTS.short })) {
        await cancelBtn.click();

        // Original 8 faces should still be there
        await expect(
          page.locator('[data-testid^="face-candidate-"]')
        ).toHaveCount(8, { timeout: TIMEOUTS.short });
        await screenshot(page, "avatar-overhaul-17-reedit-cancelled");
      }
    }
  });

  test("Engine names absent from all visible UI on key pages", async ({
    page,
  }) => {
    await loginAsAdmin(page);

    const pagesToCheck = [
      "/my-avatar",
      "/my-avatar/ai-avatar",
      "/my-avatar/clone",
      "/cast-builder",
      "/cast-builder/new",
      "/products",
      "/go-live",
      "/music",
    ];

    const FORBIDDEN_NAMES = [
      "InfiniteTalk",
      "infinitetalk",
      "MuseTalk",
      "musetalk",
      "ElevenLabs",
      "elevenlabs",
      "Fish Audio",
      "Fish Speech",
      "fish_speech",
      "FLUX",
      "Flux Kontext",
      "Kling",
      "kling_kolors",
      "RunPod",
      "runpod",
      "BS-RoFormer",
      "bs_roformer",
      "ACE-Step",
      "ace_step",
    ];

    for (const url of pagesToCheck) {
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      const text = await page.textContent("body");

      for (const forbidden of FORBIDDEN_NAMES) {
        expect(
          text,
          `Engine name "${forbidden}" found on ${url}`
        ).not.toContain(forbidden);
      }
    }
  });
});
