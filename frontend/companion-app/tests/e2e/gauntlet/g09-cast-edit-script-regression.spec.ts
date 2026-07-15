import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./helpers/auth";
import { API_BASE } from "./helpers/castFlow";

const EDIT_SCRIPT_TEXT = "The quick brown fox jumps over the lazy dog on a sunny afternoon";

test.describe("G09 — Cast Edit Script Regression (A2 Fix)", () => {
  test.setTimeout(15 * 60 * 1000);

  test("edit block script, verify edited text persists via API", async ({
    page,
  }, testInfo) => {
    await loginAsAdmin(page);

    // Create cast and generate script
    await page.goto("/cast-builder/new");
    await page.waitForLoadState("networkidle", { timeout: 60000 });

    const avatarCard = page.locator('[data-testid="avatar-card"]').first();
    await avatarCard.waitFor({ state: "visible", timeout: 30000 });
    await avatarCard.click();

    const castNameInput = page.locator('[data-testid="cast-name-input"]');
    await castNameInput.fill(`G09 Edit Script ${Date.now()}`);

    const generateBtn = page.locator('[data-testid="setup-generate-btn"]');
    await expect(generateBtn).toBeEnabled({ timeout: 5000 });
    await generateBtn.click();

    // Generate Script
    const generateScriptBtn = page.locator('button:has-text("Generate Script")');
    await expect(generateScriptBtn).toBeVisible({ timeout: 30000 });
    await generateScriptBtn.click();

    // Wait for blocks
    await page.locator('text=Block 1').waitFor({ state: "visible", timeout: 120000 });

    // Extract cast ID
    const url = page.url();
    const castIdMatch = url.match(/cast-builder\/([a-f0-9-]+)/);
    const castId = castIdMatch?.[1];
    expect(castId).toBeTruthy();

    // Get initial data to find block 1's variant
    const initialResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    expect(initialResp.ok(), "Initial cast fetch must succeed").toBeTruthy();
    const initialData = await initialResp.json();
    const block1 = initialData.blocks?.[0];
    expect(block1, "Should have at least one block").toBeTruthy();
    const variant1 = block1.variants?.[0];
    expect(variant1, "Block 1 should have at least one variant").toBeTruthy();
    const originalText = variant1.script_text;
    expect(originalText, "Variant should have original script text").toBeTruthy();

    // Edit block 1's script in the UI textarea
    const firstTextarea = page.locator("textarea").first();
    await expect(firstTextarea).toBeVisible({ timeout: 5000 });
    await firstTextarea.fill(EDIT_SCRIPT_TEXT);
    await page.waitForTimeout(1500); // debounce save

    // Trigger blur to save
    await page.locator("body").click();
    await page.waitForTimeout(1000);

    // Verify the variant was updated via API — HARD ASSERT
    const afterEditResp = await page.request.get(`${API_BASE}/casts/${castId}`);
    expect(afterEditResp.ok(), "After-edit cast fetch must succeed").toBeTruthy();
    const afterEditData = await afterEditResp.json();
    const editedVariant = afterEditData.blocks?.[0]?.variants?.[0];
    expect(
      editedVariant?.script_text,
      "Variant script_text should match edited text"
    ).toBe(EDIT_SCRIPT_TEXT);

    // Verify the placeholder text is NOT present
    expect(
      editedVariant?.script_text,
      "Should NOT contain placeholder 'Welcome to the' text"
    ).not.toContain("Welcome to the");
    expect(
      editedVariant?.script_text,
      "Should NOT contain 'Hello everyone' placeholder"
    ).not.toContain("Hello everyone");

    testInfo.annotations.push({
      type: "summary",
      description: `Script edit regression: original="${originalText?.slice(0, 50)}..." → edited="${EDIT_SCRIPT_TEXT.slice(0, 50)}..."`,
    });
  });
});
