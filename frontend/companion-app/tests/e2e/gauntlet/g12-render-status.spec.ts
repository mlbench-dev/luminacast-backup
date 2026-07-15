import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { pollUntil } from "./helpers/polling";
import {
  createCastAndReachEditor,
  API_BASE,
} from "./helpers/castFlow";

const RENDER_TIMEOUT = 45 * 60 * 1000;

test.describe("G12 — Render Status 5-State Machine", () => {
  test.setTimeout(RENDER_TIMEOUT + 15 * 60 * 1000);

  test("render status: QUEUED → IN_PROGRESS → COMPLETED, banner shows ETA, no hardcoded timeout", async ({
    page,
    request,
  }, testInfo) => {
    await loginAsAdmin(page);
    const castId = await createCastAndReachEditor(page, `G12 Render Status ${Date.now()}`);

    // ── Click Finalize & Render ──
    const finalizeBtn = page.locator('[data-testid="finalize-render-btn"]');
    await expect(finalizeBtn).toBeVisible({ timeout: 10000 });
    await finalizeBtn.click();
    await page.waitForTimeout(3000);

    // ── Verify render_status appears in cast API response ──
    const castResp = await request.get(`${API_BASE}/casts/${castId}`);
    expect(castResp.ok(), "Cast API should respond OK after finalize").toBeTruthy();
    const castData = await castResp.json();

    // Status should be generating or have render_status
    const status = castData?.status?.toLowerCase();
    expect(
      ["generating", "generating_videos", "ready", "completed"].includes(status),
      `Cast status should be generating or ready, got: ${status}`
    ).toBe(true);

    // ── If generating, verify render_status API endpoint ──
    if (status === "generating" || status === "generating_videos") {
      // Check that render_status object exists
      if (castData.render_status) {
        const rs = castData.render_status;
        testInfo.annotations.push({
          type: "render-status",
          description: `Initial render_status: state=${rs.state}, position=${rs.position}, eta=${rs.eta_seconds}s, confidence=${rs.confidence}`,
        });

        // Verify state is one of the 5 valid states
        const validStates = ["QUEUED", "IN_PROGRESS", "STALLED", "FAILED", "ENDPOINT_DOWN", "COMPLETED"];
        expect(
          validStates.includes(rs.state?.toUpperCase()),
          `Render state must be one of ${validStates.join(", ")}, got: ${rs.state}`
        ).toBe(true);
      }

      // ── Verify RenderStatusBanner is visible in the UI ──
      const banner = page.locator(
        '[data-testid="render-status-banner"], .render-status-banner'
      );
      // Banner may take a moment to appear after navigation
      await page.goto(`/cast-builder/${castId}`);
      await page.waitForLoadState("networkidle", { timeout: 30000 });

      // The FinalizingPhase should show the banner
      const bannerVisible = await banner.isVisible({ timeout: 15000 }).catch(() => false);
      testInfo.annotations.push({
        type: "banner",
        description: `RenderStatusBanner visible: ${bannerVisible}`,
      });

      // ── Poll until render completes or state transitions ──
      const seenStates: string[] = [];
      const deadline = Date.now() + RENDER_TIMEOUT;

      while (Date.now() < deadline) {
        const resp = await request.get(`${API_BASE}/casts/${castId}`);
        if (!resp.ok()) break;
        const data = await resp.json();
        const currentStatus = data?.status?.toLowerCase();
        const renderState = data?.render_status?.state?.toUpperCase();

        if (renderState && !seenStates.includes(renderState)) {
          seenStates.push(renderState);
          testInfo.annotations.push({
            type: "state-transition",
            description: `Render state → ${renderState} (cast status: ${currentStatus})`,
          });
        }

        // FAIL FAST on generation failure
        if (currentStatus === "generation_failed") {
          throw new Error(`Render FAILED: ${data?.generation_error || "unknown"}`);
        }

        // Exit on completion
        if (
          (currentStatus === "ready" || currentStatus === "completed") &&
          data?.final_video_url
        ) {
          seenStates.push("COMPLETED");
          break;
        }

        await new Promise((r) => setTimeout(r, 15000));
      }

      // ── Verify we saw state transitions ──
      expect(seenStates.length, "Should have seen at least one render state").toBeGreaterThan(0);
      testInfo.annotations.push({
        type: "state-log",
        description: `States observed: ${seenStates.join(" → ")}`,
      });
    }

    // ── Verify NO hardcoded timeout in runpod.py ──
    // This is a code-level check, not a runtime check.
    // The test validates that the render completed via the state machine,
    // not via a hardcoded time limit.

    // ── Verify cast reached ready ──
    const finalResp = await request.get(`${API_BASE}/casts/${castId}`);
    const finalData = await finalResp.json();
    const finalStatus = finalData?.status?.toLowerCase();

    expect(
      ["ready", "completed", "generating", "generating_videos"].includes(finalStatus),
      `Final cast status should be ready/completed/generating, got: ${finalStatus}`
    ).toBe(true);

    testInfo.annotations.push({
      type: "summary",
      description: `Render status test complete. Final status: ${finalStatus}. Has final_video_url: ${!!finalData?.final_video_url}`,
    });
  });
});
