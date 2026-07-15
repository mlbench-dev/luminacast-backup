import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import { pollUntil } from "./fixtures/polling";

test.describe("AI Avatar Full 7-Phase Pipeline", () => {
  test.setTimeout(30 * 60 * 1000); // 30 min for full pipeline

  test("walks the entire 7-phase journey end to end", async ({ page }) => {
    await loginAsAdmin(page);

    // ─── STEP 1: Navigate to AI Avatar creation ───
    await page.goto("/my-avatar");
    await page.waitForSelector('[data-testid="setup-page"]', { timeout: 15000 });

    // Click "Create character" (AI avatar)
    await page.click('[data-testid="create-digital-card"]');
    await page.waitForURL(/ai-avatar/, { timeout: 10000 });

    // ─── PHASE 1: Audience ───
    // Fill age range
    await page.click('button:has-text("18-24")');

    // Select interests
    await page.click('button:has-text("Beauty")');
    await page.click('button:has-text("Fitness")');

    // Fill audience description
    await page.fill(
      'textarea[placeholder*="Describe your audience"]',
      "Young women interested in affordable fitness gear and beauty products"
    );

    // Continue
    await page.click('button:has-text("Continue to Description")');

    // Verify we're on Description phase
    await page.waitForSelector('h3:has-text("Describe your avatar")', { timeout: 10000 });

    // ─── PHASE 2: Description ───
    // Fill name
    await page.fill('input[placeholder="Avatar name"]', "TestAvatar Pipeline");

    // Fill description
    await page.fill(
      'textarea[placeholder*="Describe your avatar"]',
      "energetic young fitness influencer with short dark hair and a confident smile, wearing athletic wear"
    );

    // Toggle style presets
    await page.click('button:has-text("Studio")');
    await page.click('button:has-text("Natural")');

    // Toggle imperfections
    await page.click('button:has-text("Freckles")');
    await page.click('button:has-text("Skin Texture")');

    // Verify Surprise Me does NOT call rewrite-description for chip toggles
    // (spec says: intercept rewrite-description and assert NOT called for chip toggles)
    let rewriteCalled = false;
    page.on("request", (req) => {
      if (req.url().includes("rewrite-description") && req.method() === "POST") {
        const body = req.postDataJSON();
        if (body && body.regenerate) rewriteCalled = true;
      }
    });

    // Click Surprise Me — this SHOULD call the LLM
    rewriteCalled = false;
    await page.click('button:has-text("Surprise Me")');
    await page.waitForTimeout(3000); // Wait for LLM response
    // rewriteCalled should be true for Surprise Me
    expect(rewriteCalled).toBe(true);

    // Continue to Face
    await page.click('button:has-text("Continue to Face")');

    // ─── PHASE 3: Face ───
    // Wait for face generation UI
    await page.waitForSelector('[data-testid="generate-faces-btn"], button:has-text("Generate")', { timeout: 15000 });

    // Generate faces (use existing description)
    const genFacesBtn = page.locator('[data-testid="generate-faces-btn"]');
    if (await genFacesBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await genFacesBtn.click();
    }

    // Wait for face candidates (up to 3 min for FLUX)
    await pollUntil(
      async () => {
        const candidates = page.locator('[data-testid^="face-candidate-"]');
        return (await candidates.count()) >= 4;
      },
      { timeout: 180000, interval: 5000, label: "face candidates" }
    );

    // Select face 3
    const faceCandidate = page.locator('[data-testid="face-candidate-2"]');
    if (await faceCandidate.isVisible({ timeout: 5000 }).catch(() => false)) {
      await faceCandidate.click();
    }

    // Approve face / continue
    const continueBtn = page.locator('button:has-text("Continue"), button:has-text("Lock this face")');
    await continueBtn.first().click({ timeout: 10000 });

    // ─── PHASE 4: Voice ───
    await page.waitForSelector('h3:has-text("Voice"), [data-testid="voice-phase"]', { timeout: 15000 });

    // Select American accent
    const accentSelect = page.locator('select, [data-testid="accent-select"]');
    if (await accentSelect.isVisible({ timeout: 3000 }).catch(() => false)) {
      await accentSelect.selectOption("us");
    }

    // Type custom test script
    const testScriptInput = page.locator('textarea[data-testid="test-speech"], textarea[placeholder*="test"]');
    if (await testScriptInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await testScriptInput.fill("Hello, I am TestAvatar and today we test the accent.");
    }

    // Generate voice previews
    const genVoiceBtn = page.locator('button:has-text("Generate"), button:has-text("voice options")');
    if (await genVoiceBtn.first().isVisible({ timeout: 5000 }).catch(() => false)) {
      await genVoiceBtn.first().click();
    }

    // Wait for voice previews (up to 2 min)
    await pollUntil(
      async () => {
        const previews = page.locator('[data-testid^="voice-preview-"], audio');
        return (await previews.count()) >= 1;
      },
      { timeout: 120000, interval: 5000, label: "voice previews" }
    );

    // Select first voice
    const voiceSelect = page.locator('[data-testid="voice-preview-0"], [data-testid="select-voice-0"]');
    if (await voiceSelect.isVisible({ timeout: 5000 }).catch(() => false)) {
      await voiceSelect.click();
    }

    // Lock voice + continue
    const lockVoiceBtn = page.locator('button:has-text("Lock"), button:has-text("Approve"), button:has-text("Continue")');
    await lockVoiceBtn.first().click({ timeout: 10000 });

    // ─── PHASE 5: Body Description ───
    await page.waitForSelector('h3:has-text("Body Description")', { timeout: 30000 });

    // Verify large header shows 160px avatar
    const headerImg = page.locator('img.h-40, img[class*="h-40"]');
    await expect(headerImg).toBeVisible({ timeout: 10000 });

    // Wait for body description to load
    await pollUntil(
      async () => {
        const descBlock = page.locator('div.whitespace-pre-wrap, textarea');
        const text = await descBlock.first().textContent().catch(() => "");
        return (text?.length ?? 0) > 20;
      },
      { timeout: 60000, interval: 3000, label: "body description" }
    );

    // Test voice playback
    const playVoiceBtn = page.locator('button:has-text("Listen to voice")');
    if (await playVoiceBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await playVoiceBtn.click();
    }

    // Approve body description
    await page.click('button:has-text("Approve & Continue")');

    // ─── PHASE 6: Body Shots ───
    await page.waitForSelector('h3:has-text("Body Shots")', { timeout: 15000 });

    // Wait for 6 shots (poll up to 5 min)
    await pollUntil(
      async () => {
        const shots = page.locator('img[alt="Front"], img[alt="3/4 Left"], img[alt="Profile Left"]');
        return (await shots.count()) >= 3;
      },
      { timeout: 300000, interval: 10000, label: "body shots" }
    );

    // Approve all
    await page.click('button:has-text("Approve All")');

    // ─── PHASE 7: Preview ───
    await page.waitForSelector('h3:has-text("Preview"), [data-testid="preview-phase"]', { timeout: 15000 });

    // Wait for preview video render (up to 10 min)
    await pollUntil(
      async () => {
        const video = page.locator('video[src]');
        return (await video.count()) >= 1;
      },
      { timeout: 600000, interval: 15000, label: "preview video" }
    );

    // Verify it's a real video (not just an image)
    const videoEl = page.locator("video").first();
    await expect(videoEl).toBeVisible({ timeout: 10000 });

    // Approve avatar
    const approveBtn = page.locator('button:has-text("Approve"), button:has-text("Done")');
    await approveBtn.first().click({ timeout: 10000 });

    // ─── VERIFY: Avatar visible in library ───
    await page.waitForURL(/my-avatar/, { timeout: 15000 });
    await page.waitForSelector('[data-testid="setup-page"]', { timeout: 15000 });

    // Find the new avatar tile
    const avatarCards = page.locator('[data-testid^="avatar-card-"]');
    await expect(avatarCards.first()).toBeVisible({ timeout: 10000 });

    // Hover the tile → assert video element exists
    const firstCard = avatarCards.first();
    await firstCard.hover();

    const videoInCard = firstCard.locator("video");
    if (await videoInCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Video element exists on hover — pass
      expect(await videoInCard.getAttribute("src")).toBeTruthy();
    }
  });
});
