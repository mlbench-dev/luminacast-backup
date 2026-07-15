import { Page, expect } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL || "https://www.luminacast.com";
export const API_BASE = `${BASE_URL}/api`;

/**
 * Walk Setup → Script → Audio → Editor (Arrange phase).
 * Returns the cast ID extracted from the URL.
 */
export async function createCastAndReachEditor(
  page: Page,
  castName?: string
): Promise<string> {
  // Setup phase
  await page.goto("/cast-builder/new");
  await page.waitForLoadState("networkidle", { timeout: 60000 });

  // Select first avatar
  const avatarCard = page.locator('[data-testid="avatar-card"]').first();
  await avatarCard.waitFor({ state: "visible", timeout: 30000 });
  await avatarCard.click();
  await page.waitForTimeout(500);

  // Fill cast name
  const castNameInput = page.locator('[data-testid="cast-name-input"]');
  await castNameInput.fill(castName || `Gauntlet Cast ${Date.now()}`);

  // Click Generate Script (setup page)
  const generateBtn = page.locator('[data-testid="setup-generate-btn"]');
  await expect(generateBtn).toBeEnabled({ timeout: 5000 });
  await generateBtn.click();

  // Script Phase: click Generate Script
  const generateScriptBtn = page.locator('button:has-text("Generate Script")');
  await expect(generateScriptBtn).toBeVisible({ timeout: 30000 });
  await generateScriptBtn.click();

  // Wait for script blocks to appear
  const scriptBlock = page.locator('text=Block 1');
  await scriptBlock.waitFor({ state: "visible", timeout: 120000 });

  // Click Generate Audio
  const audioBtn = page.locator('button:has-text("Generate Audio")');
  await expect(audioBtn).toBeVisible({ timeout: 10000 });
  await audioBtn.click();

  // Wait for editor shell
  const editorShell = page.locator('[data-testid="editor-shell"]');
  await editorShell.waitFor({ state: "visible", timeout: 300000 });

  // Extract cast ID
  const url = page.url();
  const castIdMatch = url.match(/cast-builder\/([a-f0-9-]+)/);
  const castId = castIdMatch?.[1];
  expect(castId, "Cast ID should be in URL").toBeTruthy();
  return castId!;
}

/**
 * Walk Setup → Script phase only. Returns cast ID.
 */
export async function createCastAndReachScript(
  page: Page,
  castName?: string
): Promise<string> {
  await page.goto("/cast-builder/new");
  await page.waitForLoadState("networkidle", { timeout: 60000 });

  const avatarCard = page.locator('[data-testid="avatar-card"]').first();
  await avatarCard.waitFor({ state: "visible", timeout: 30000 });
  await avatarCard.click();
  await page.waitForTimeout(500);

  const castNameInput = page.locator('[data-testid="cast-name-input"]');
  await castNameInput.fill(castName || `Gauntlet Cast ${Date.now()}`);

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

  const url = page.url();
  const castIdMatch = url.match(/cast-builder\/([a-f0-9-]+)/);
  const castId = castIdMatch?.[1];
  expect(castId, "Cast ID should be in URL").toBeTruthy();
  return castId!;
}

/**
 * From the editor, click Finalize & Render, poll for completion, navigate to Ready.
 *
 * HONEST exit condition: cast.status === "ready" AND cast.final_video_url is set.
 * Per-variant clip_url/stream_url are Audio Ready artifacts, NOT final renders.
 */
export async function finalizeAndWaitForRender(
  page: Page,
  castId: string,
  timeoutMs: number = 45 * 60 * 1000
): Promise<any> {
  const finalizeBtn = page.locator('[data-testid="finalize-render-btn"]');
  await expect(finalizeBtn).toBeVisible({ timeout: 10000 });
  await finalizeBtn.click();

  const deadline = Date.now() + timeoutMs;
  let castData: any = null;
  let lastStatus = "";
  while (Date.now() < deadline) {
    const response = await page.request.get(`${API_BASE}/casts/${castId}`);
    if (!response.ok()) {
      throw new Error(`API error polling cast ${castId}: ${response.status()} ${response.statusText()}`);
    }
    castData = await response.json();
    const currentStatus = castData?.status?.toLowerCase() || "";

    if (currentStatus !== lastStatus) {
      console.log(`[finalizeAndWaitForRender] cast ${castId}: status=${currentStatus}, final_video_url=${castData?.final_video_url ? 'SET' : 'null'}`);
      lastStatus = currentStatus;
    }

    // FAIL FAST on generation failure
    if (currentStatus === "generation_failed") {
      throw new Error(`Render FAILED for cast ${castId}: ${castData?.generation_error || 'unknown error'}`);
    }

    // ONLY exit when status is ready AND final_video_url is populated
    const isReady = currentStatus === "ready" || currentStatus === "completed";
    const hasFinalVideo = !!castData?.final_video_url;
    if (isReady && hasFinalVideo) {
      return castData;
    }

    await new Promise((r) => setTimeout(r, 15000));
  }
  throw new Error(
    `Render timed out after ${timeoutMs}ms for cast ${castId}. ` +
    `Last status: ${lastStatus}, final_video_url: ${castData?.final_video_url || 'null'}`
  );
}

/**
 * Extract the final composited video URL from cast data.
 *
 * Uses cast-level final_video_url (set on render completion).
 * Falls back to first variant with final_video_key.
 * Does NOT use stream_url or clip_url (those are Audio Ready per-block clips).
 */
export function extractVideoUrl(castData: any): string {
  // Prefer cast-level final video URL
  if (castData.final_video_url) return castData.final_video_url;

  // Fallback: first variant with final_video_key or video_key
  for (const block of castData.blocks || []) {
    for (const variant of block.variants || []) {
      if (variant.final_video_key)
        return `https://media.luminacast.com/${variant.final_video_key}`;
      if (variant.video_key)
        return `https://media.luminacast.com/${variant.video_key}`;
    }
  }
  throw new Error("No final video URL found in cast data — render may not have completed");
}
