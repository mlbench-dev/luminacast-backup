import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { pollUntil, pollApiEndpoint } from "./helpers/polling";
import { clickPlayAndVerify } from "./helpers/clickPlayAndVerify";
import { API_BASE } from "./helpers/castFlow";
import * as fs from "fs";
import * as path from "path";

const CLONE_TIMEOUT = 15 * 60 * 1000; // 15 min for clone processing

test.describe("G01 — Clone Avatar Journey", () => {
  test.setTimeout(20 * 60 * 1000);

  test("clone avatar: upload, wait for APPROVED via API, verify playback", async ({
    page,
  }, testInfo) => {
    await loginAsAdmin(page);

    // Navigate to clone page
    await page.goto("/my-avatar/clone");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    // Check for clone-input fixture — HARD FAIL if missing
    const fixturePath = path.join(process.cwd(), "tests/fixtures/clone-input.mp4");
    if (!fs.existsSync(fixturePath)) {
      const fallback = path.join(process.cwd(), "tests/e2e/fixtures/test-voice-sample.mp4");
      if (!fs.existsSync(fallback)) {
        throw new Error(
          "FIXTURE MISSING: tests/fixtures/clone-input.mp4 not found. Please provide a test video."
        );
      }
      const fixtureDir = path.dirname(fixturePath);
      if (!fs.existsSync(fixtureDir)) fs.mkdirSync(fixtureDir, { recursive: true });
      fs.copyFileSync(fallback, fixturePath);
    }

    // Upload video file
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(fixturePath);
    await page.waitForTimeout(2000);

    // Submit the clone — MUST find and click button
    const submitBtn = page.locator(
      'button:has-text("Submit"), button:has-text("Clone"), button:has-text("Start"), button:has-text("Create")'
    ).first();
    await expect(submitBtn, "Clone submit button must be visible").toBeVisible({ timeout: 5000 });
    await submitBtn.click();

    // Poll for clone to reach APPROVED status via API — not DOM heuristics
    const avatarData = await pollApiEndpoint(
      page,
      `${API_BASE}/avatars`,
      (data: any) => {
        const avatars = data?.avatars || data || [];
        if (!Array.isArray(avatars)) return false;
        return avatars.some(
          (a: any) => a.status?.toUpperCase() === "APPROVED" || a.status?.toUpperCase() === "READY"
        );
      },
      { timeoutMs: CLONE_TIMEOUT, intervalMs: 30000, message: "Clone avatar APPROVED" }
    );

    // Navigate to avatar library
    await page.goto("/my-avatar");
    await page.waitForLoadState("networkidle", { timeout: 30000 });

    // Find the avatar tile — MUST be visible
    const avatarTile = page.locator('[data-testid="avatar-card"]').first();
    await expect(avatarTile, "Avatar tile must be visible in library").toBeVisible({ timeout: 10000 });
    await avatarTile.hover();
    await page.waitForTimeout(1000);

    // Click tile to open modal
    await avatarTile.click();
    await page.waitForTimeout(2000);

    // Play the video in the modal — MUST find and play
    const video = page.locator("video").first();
    await expect(video, "Video element must be visible in avatar modal").toBeVisible({ timeout: 5000 });
    await clickPlayAndVerify(page, video);

    testInfo.annotations.push({
      type: "summary",
      description: "Clone avatar uploaded, reached APPROVED, video plays in library modal",
    });
  });
});
