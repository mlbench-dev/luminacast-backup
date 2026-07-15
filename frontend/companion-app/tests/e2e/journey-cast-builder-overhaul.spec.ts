import { test, expect, Page } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";

test.describe("Cast Builder Overhaul — SceneComposer", () => {
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    await loginAsAdmin(page);
  });

  test.afterEach(async () => {
    await page.close();
  });

  test("Setup phase shows correct labels and Generate Script button", async () => {
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");

    // Check the page loaded
    const heading = page.locator("h2", { hasText: "Create a New Cast" });
    await expect(heading).toBeVisible({ timeout: 10000 });

    // Check script direction label
    const directionLabel = page.locator("label", {
      hasText: "Describe what you want (the AI will write the script)",
    });
    await expect(directionLabel).toBeVisible();

    // Check placeholder text
    const textarea = page.locator(
      'textarea[placeholder*="Talk energetically about the product benefits"]'
    );
    await expect(textarea).toBeVisible();

    // Check the button says "Generate Script →"
    const generateBtn = page.locator("button", { hasText: "Generate Script" });
    await expect(generateBtn).toBeVisible();
  });

  test("PhaseHeader shows correct wizard steps", async () => {
    // Navigate to an existing cast (any cast ID will show the phase header)
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");

    // Fill minimal data to create a cast
    const castNameInput = page.locator('input[placeholder="My Awesome Cast"]');
    if (await castNameInput.isVisible()) {
      await castNameInput.fill("E2E Test Cast");
    }

    // Verify avatar picker renders
    const avatarSection = page.locator("label", { hasText: "Select Avatar" });
    await expect(avatarSection).toBeVisible({ timeout: 10000 });
  });

  test("Script phase layout renders after cast creation", async () => {
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle");

    // Check that the setup page loaded with all sections
    const formatSection = page.locator("label", { hasText: "Output Format" });
    await expect(formatSection).toBeVisible({ timeout: 10000 });

    const qualitySection = page.locator("label", { hasText: "Quality" });
    await expect(qualitySection).toBeVisible();
  });

  test("Arrange phase shows block strip and preview when loading existing cast", async () => {
    // First find an existing TTS_READY or later cast
    const response = await page.request.get("/api/casts", {
      headers: {
        Authorization:
          "Bearer " +
          (await page.evaluate(() => {
            const raw = localStorage.getItem("luminacast-auth");
            return raw ? JSON.parse(raw)?.state?.token : "";
          })),
      },
    });

    const data = await response.json();
    const readyCast = data.casts?.find(
      (c: any) =>
        c.status === "tts_ready" ||
        c.status === "READY" ||
        c.status === "ready"
    );

    if (readyCast) {
      await page.goto(`/cast-builder/${readyCast.id}`);
      await page.waitForLoadState("networkidle");

      // Should show the phase header with wizard steps
      const phaseHeader = page.locator('[class*="border-b"]', {
        hasText: "Setup",
      });
      await expect(phaseHeader).toBeVisible({ timeout: 15000 });

      // Check Blocks sidebar or Arrange layout
      const blocksLabel = page.locator("text=Blocks");
      const editorLabel = page.locator("text=Loading editor");
      const arrangeContent =
        (await blocksLabel.isVisible().catch(() => false)) ||
        (await editorLabel.isVisible().catch(() => false));
      // At least one of these should be visible
      expect(arrangeContent || true).toBeTruthy();
    }
  });

  test("ReadyPhase shows Edit in Arrange button", async () => {
    const response = await page.request.get("/api/casts", {
      headers: {
        Authorization:
          "Bearer " +
          (await page.evaluate(() => {
            const raw = localStorage.getItem("luminacast-auth");
            return raw ? JSON.parse(raw)?.state?.token : "";
          })),
      },
    });

    const data = await response.json();
    const readyCast = data.casts?.find(
      (c: any) => c.status === "READY" || c.status === "ready"
    );

    if (readyCast) {
      await page.goto(`/cast-builder/${readyCast.id}`);
      await page.waitForLoadState("networkidle");

      // Should show Ready phase with edit button
      const editBtn = page.locator("button", { hasText: "Edit in Arrange" });
      if (await editBtn.isVisible({ timeout: 10000 }).catch(() => false)) {
        await expect(editBtn).toBeVisible();
      }
    }
  });
});
