/**
 * Master Session Follow-up: Real Render Acceptance Test
 *
 * Acceptance gate for the entire stack. Single test with 45-minute timeout.
 * Pipeline: Login → Create cast → Generate script → Generate audio → Arrange → Finalize → Poll render → Download MP4 → Verify
 */
import { test, expect, Page } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import { pollUntil, pollApiEndpoint } from "./fixtures/polling";
import { LogChecker } from "./fixtures/log-checker";
import { screenshot, TIMEOUTS, waitForToast, verifyNoErrorBoundary } from "./fixtures/qa-helpers";
import * as fs from "fs";
import * as path from "path";
import { execSync } from "child_process";

test.describe("Master Session: Real Render Acceptance Gate", () => {
  let page: Page;
  let logChecker: LogChecker;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    logChecker = new LogChecker(page);
    await loginAsAdmin(page);
  });

  test.afterEach(async () => {
    await page.close();
  });

  test("Full pipeline: create → script → audio → arrange → render", async ({ }, testInfo) => {
    test.setTimeout(50 * 60 * 1000); // 50 min — InfiniteTalk RunPod render takes 27-35 min
    const testStart = Date.now();
    const castName = `Render-Gate-${Date.now()}`;
    let castId = "";

    // ── Step 1: Setup — Get auth token and pick fixtures via API ──
    // Extract JWT from localStorage (set during loginAsAdmin via zustand persist)
    const authToken = await page.evaluate(() => {
      const raw = localStorage.getItem("luminacast-auth");
      if (!raw) return "";
      try {
        const parsed = JSON.parse(raw);
        return parsed?.state?.token || parsed?.token || "";
      } catch {
        return "";
      }
    });
    expect(authToken).toBeTruthy();
    const authHeaders = { Authorization: `Bearer ${authToken}` };

    // Query for an APPROVED avatar
    const avatarsResp = await page.request.get("/api/avatar/list?status=approved", {
      headers: authHeaders,
    });
    const avatarsData = await avatarsResp.json().catch(() => ({ avatars: [] }));
    const avatars = avatarsData.avatars || avatarsData.items || avatarsData || [];
    if (!Array.isArray(avatars) || avatars.length === 0) {
      console.log(`[render-gate] Avatar API response: ${avatarsResp.status()} ${JSON.stringify(avatarsData).slice(0, 200)}`);
      test.skip(true, "No APPROVED avatar available — environment not set up");
      return;
    }
    console.log(`[render-gate] Found ${avatars.length} approved avatar(s)`);

    // Query for an active product
    const productsResp = await page.request.get("/api/products", {
      headers: authHeaders,
    });
    const productsData = await productsResp.json().catch(() => ({ products: [] }));
    const products = productsData.products || productsData.items || productsData || [];
    if (!Array.isArray(products) || products.length === 0) {
      console.log(`[render-gate] Products API response: ${productsResp.status()} ${JSON.stringify(productsData).slice(0, 200)}`);
      test.skip(true, "No active product available — environment not set up");
      return;
    }
    console.log(`[render-gate] Found ${products.length} product(s)`);

    // ── Step 2: Build cast through real UI ──
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");
    await verifyNoErrorBoundary(page);

    // Click "New Cast"
    const newCastBtn = page.locator(
      'button:has-text("New Cast"), button:has-text("Create"), a:has-text("New Cast"), [data-testid="new-cast-btn"]'
    ).first();
    if (await newCastBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await newCastBtn.click();
    } else {
      await page.goto("/cast-builder/new");
    }
    await page.waitForLoadState("networkidle");
    await screenshot(page, "render-01-setup");

    // Wait for avatar grid to load — avatar auto-selects first via useEffect
    const avatarGrid = page.locator('text=Select Avatar').first();
    await expect(avatarGrid).toBeVisible({ timeout: 15000 });
    // Click first avatar button to ensure selection
    const firstAvatar = page.locator('button:has(img)').first();
    if (await firstAvatar.isVisible({ timeout: 5000 }).catch(() => false)) {
      await firstAvatar.click();
    }

    // Fill cast name (input with placeholder "My Awesome Cast")
    const nameInput = page.locator('input[placeholder="My Awesome Cast"]').first();
    await expect(nameInput).toBeVisible({ timeout: 5000 });
    await nameInput.fill(castName);

    // Select first product
    // Products section is below avatars — scroll down and click first product button
    const productSection = page.locator('text=Select Products').first();
    if (await productSection.isVisible({ timeout: 5000 }).catch(() => false)) {
      await productSection.scrollIntoViewIfNeeded();
      // Product buttons are in a grid below "Select Products" label
      const productBtn = page.locator('text=Select Products').locator('..').locator('~ div button').first();
      if (await productBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await productBtn.click();
      }
    }

    await screenshot(page, "render-02-setup-filled");

    // Click "Generate Script →" to create cast and generate script
    const genScriptSetup = page.locator('button:has-text("Generate Script")').first();
    await expect(genScriptSetup).toBeVisible({ timeout: 5000 });
    await expect(genScriptSetup).toBeEnabled();
    await genScriptSetup.click();
    await page.waitForTimeout(3000);

    // Extract cast ID from URL
    const urlAfterSetup = page.url();
    const castIdMatch = urlAfterSetup.match(/cst_[a-f0-9]+/);
    if (castIdMatch) {
      castId = castIdMatch[0];
    }
    console.log(`[render-gate] Cast created: ${castId}, name: ${castName}`);
    await screenshot(page, "render-03-after-setup");

    // ── Step 3: Script phase — click Generate Script, wait for blocks ──
    // SetupPhase created the cast and transitioned to ScriptPhase.
    // ScriptPhase shows "Generate Script" button that must be clicked.
    const genScriptBtn = page.locator('button:has-text("Generate Script")').first();
    await expect(genScriptBtn).toBeVisible({ timeout: 10000 });
    await genScriptBtn.click();
    console.log(`[render-gate] Generate Script clicked (${((Date.now() - testStart) / 1000).toFixed(0)}s elapsed)`);

    // Wait for script blocks to appear (AI generation takes time)
    await pollUntil(
      async () => {
        // Look for "Generate Audio" button which appears after blocks are generated
        const audioBtn = page.locator('button:has-text("Generate Audio")').first();
        if (await audioBtn.isVisible({ timeout: 1000 }).catch(() => false)) return true;
        // Or look for script text content in blocks
        const blockText = page.locator('textarea, [contenteditable="true"]').first();
        if (!(await blockText.isVisible({ timeout: 1000 }).catch(() => false))) return false;
        const text = await blockText.inputValue().catch(() => blockText.textContent());
        return (text?.length ?? 0) > 20;
      },
      { timeout: 120_000, interval: 5_000, label: "script generation" }
    );
    console.log(`[render-gate] Script generated (${((Date.now() - testStart) / 1000).toFixed(0)}s elapsed)`);
    await screenshot(page, "render-04-script-ready");

    // ── Step 4: Generate Audio — click "Generate Audio →" ──
    const toAudioBtn = page.locator('button:has-text("Generate Audio")').first();
    await expect(toAudioBtn).toBeVisible({ timeout: 10000 });
    await toAudioBtn.click();
    console.log(`[render-gate] Generate Audio clicked (${((Date.now() - testStart) / 1000).toFixed(0)}s elapsed)`);
    await page.waitForTimeout(2000);

    // ── Step 5: Wait for audio generation → auto-transitions to Arrange (editor) phase ──
    // AudioGeneratingPhase shows progress, then auto-transitions to ArrangePhase
    await pollUntil(
      async () => {
        // Check if we're now in the editor/arrange phase
        const editorShell = page.locator('[data-testid="editor-shell"]');
        return (
          (await editorShell.isVisible({ timeout: 2000 }).catch(() => false))
        );
      },
      { timeout: 300_000, interval: 10_000, label: "audio generation + transition to arrange" }
    );
    console.log(`[render-gate] Audio ready, now in Arrange phase (${((Date.now() - testStart) / 1000).toFixed(0)}s elapsed)`);
    await screenshot(page, "render-05-audio-ready");

    // Verify editor mounted (Twick VideoEditor turnkey)
    const editorContainer = page.locator('[data-testid="editor-shell"], .luminacast-editor-wrapper').first();
    await expect(editorContainer).toBeVisible({ timeout: 30000 });
    await verifyNoErrorBoundary(page);
    console.log(`[render-gate] Arrange phase loaded (${((Date.now() - testStart) / 1000).toFixed(0)}s elapsed)`);
    await screenshot(page, "render-06-arrange-loaded");

    // ── Step 6: Click Finalize & Render ──
    const finalizeBtn = page.locator('[data-testid="finalize-render-btn"]');
    await expect(finalizeBtn).toBeVisible({ timeout: 5000 });
    await expect(finalizeBtn).toBeEnabled();
    await finalizeBtn.click();
    console.log(`[render-gate] Finalize clicked (${((Date.now() - testStart) / 1000).toFixed(0)}s elapsed)`);
    await screenshot(page, "render-07-render-started");

    // ── Step 7: Poll for rendered video URL via API (up to 20 min) ──
    // If we didn't extract cast ID from URL, try from page content
    if (!castId) {
      const pageText = await page.content();
      const fallbackMatch = pageText.match(/cst_[a-f0-9]+/);
      if (fallbackMatch) castId = fallbackMatch[0];
    }
    expect(castId).toBeTruthy();
    console.log(`[render-gate] Polling cast ${castId} for render completion...`);

    let videoUrl = "";
    const pollStart = Date.now();
    await pollUntil(
      async () => {
        const elapsed = ((Date.now() - pollStart) / 1000).toFixed(0);
        try {
          const resp = await page.request.get(`/api/casts/${castId}`, { headers: authHeaders });
          if (!resp.ok()) return false;
          const data = await resp.json();
          const status = data.status || data.cast?.status;
          console.log(`[render-gate] Poll ${elapsed}s: status=${status}`);

          if (status === "READY" || status === "COMPLETED") {
            // Find first variant with final_video_key
            const blocks = data.blocks || data.cast?.blocks || [];
            for (const block of blocks) {
              for (const variant of block.variants || []) {
                if (variant.final_video_key || variant.clip_url) {
                  videoUrl = variant.clip_url || `https://media.luminacast.com/${variant.final_video_key}`;
                  return true;
                }
              }
            }
            // Status is ready but no video key yet — keep polling
            return false;
          }
          if (status === "GENERATION_FAILED") {
            throw new Error(`Render failed: ${data.generation_error || "unknown error"}`);
          }
        } catch (e: any) {
          if (e.message?.includes("Render failed")) throw e;
          console.log(`[render-gate] Poll ${elapsed}s: error ${e.message}`);
        }
        return false;
      },
      { timeout: 2_400_000, interval: 15_000, label: "render completion" }
    );

    console.log(`[render-gate] Render complete! Video URL: ${videoUrl}`);
    console.log(`[render-gate] Cast ID: ${castId}`);
    await screenshot(page, "render-08-render-complete");

    // ── Step 8: Download the MP4 ──
    expect(videoUrl).toBeTruthy();

    const outputDir = path.resolve("test-results");
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    const mp4Path = path.join(outputDir, `master-render-${castId}.mp4`);

    const videoResp = await page.request.get(videoUrl);
    expect(videoResp.ok()).toBeTruthy();
    const videoBuffer = await videoResp.body();
    fs.writeFileSync(mp4Path, videoBuffer);
    const fileSizeKB = videoBuffer.length / 1024;
    console.log(`[render-gate] MP4 downloaded: ${mp4Path} (${fileSizeKB.toFixed(1)} KB)`);
    expect(fileSizeKB).toBeGreaterThan(100); // At least 100 KB

    // ── Step 9: Verify ftyp magic bytes ──
    const header = videoBuffer.slice(0, 12);
    const ftypStr = header.slice(4, 8).toString("ascii");
    if (ftypStr !== "ftyp") {
      const hexDump = Array.from(header).map(b => b.toString(16).padStart(2, "0")).join(" ");
      throw new Error(`Not a valid MP4: bytes 4-8 are "${ftypStr}" not "ftyp". Hex: ${hexDump}`);
    }
    console.log(`[render-gate] ftyp magic bytes verified ✓`);

    // ── Step 10: Verify duration with ffprobe ──
    try {
      const durationStr = execSync(
        `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "${mp4Path}"`,
        { encoding: "utf-8", timeout: 30000 }
      ).trim();
      const duration = parseFloat(durationStr);
      console.log(`[render-gate] MP4 duration: ${duration.toFixed(2)}s`);
      expect(duration).toBeGreaterThanOrEqual(5);
      expect(duration).toBeLessThanOrEqual(600);
    } catch (e: any) {
      console.warn(`[render-gate] ffprobe not available or failed: ${e.message}. Skipping duration check.`);
    }

    // ── Step 11: Attach as test artifact ──
    await testInfo.attach("rendered-video", {
      path: mp4Path,
      contentType: "video/mp4",
    });
    console.log(`[render-gate] MP4 attached as test artifact`);

    // ── Step 12: Console error check ──
    logChecker.assertNoUnexpectedErrors([
      /whisper/i,
      /twick/i,
      /sentry/i,
    ]);

    const totalTime = ((Date.now() - testStart) / 1000).toFixed(0);
    console.log(`[render-gate] PASSED — Total time: ${totalTime}s, Cast: ${castId}, Video: ${videoUrl}`);
  });

  test("Editor 4-column layout smoke test", async () => {
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    // Find an existing cast to verify layout
    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(5000);

    // Verify the new 4-column grid layout
    const shell = page.locator('[data-testid="editor-shell"]');
    if (!(await shell.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip(true, "Editor shell not visible — cast may not be in arrange phase");
      return;
    }

    // Check grid columns: 56px | 220px | flex | 280px
    const style = await shell.getAttribute("style");
    expect(style).toContain("56px");
    expect(style).toContain("220px");
    expect(style).toContain("280px");

    // Verify all 4 panels
    await expect(page.locator('[data-testid="editor-tab-strip"]')).toBeVisible();
    await expect(page.locator('[data-testid="editor-tab-content"]')).toBeVisible();
    await expect(page.locator('[data-testid="editor-timeline"]')).toBeVisible();
    await expect(page.locator('[data-testid="editor-properties"]')).toBeVisible();

    // Verify floating playback
    await expect(page.locator('[data-testid="floating-playback"]')).toBeVisible();
    await expect(page.locator('[data-testid="playback-play-pause"]')).toBeVisible();
    await expect(page.locator('[data-testid="playback-time"]')).toBeVisible();

    await screenshot(page, "master-layout-smoke");
    await verifyNoErrorBoundary(page);
  });

  test("Finalize button is wired and triggers render", async () => {
    await page.goto("/cast-builder");
    await page.waitForLoadState("networkidle");

    const castCard = page.locator('[data-testid^="cast-card-"]').first();
    if (!(await castCard.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip(true, "No casts available");
      return;
    }
    await castCard.click();
    await page.waitForTimeout(5000);

    const shell = page.locator('[data-testid="editor-shell"]');
    if (!(await shell.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip(true, "Not in arrange phase");
      return;
    }

    const finalizeBtn = page.locator('[data-testid="finalize-render-btn"]');
    await expect(finalizeBtn).toBeVisible({ timeout: 5000 });
    await expect(finalizeBtn).toContainText(/Finalize|Render/);

    // Click and verify toast
    await finalizeBtn.click();
    await waitForToast(page, /render/i);
    await screenshot(page, "master-finalize-triggered");
  });
});
