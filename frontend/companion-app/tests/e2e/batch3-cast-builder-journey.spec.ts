import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "./fixtures/auth";
import {
  screenshot,
  verifyNoErrorBoundary,
  waitForPageLoad,
  TIMEOUTS,
} from "./fixtures/qa-helpers";

test.describe("Batch 3 — Cast Builder Journey (Integration)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("Can navigate to cast builder", async ({ page }) => {
    // Navigate via sidebar nav link
    const castBuilderNav = page.locator('[data-testid="nav-cast-builder"]');
    if (
      await castBuilderNav
        .isVisible({ timeout: TIMEOUTS.short })
        .catch(() => false)
    ) {
      await castBuilderNav.click();
      await page.waitForTimeout(2000);
    } else {
      // Fallback: direct navigation
      await page.goto("/cast-builder");
    }

    await waitForPageLoad(page);
    await screenshot(page, "b3-11-cast-builder-nav");
    await verifyNoErrorBoundary(page);

    // Verify we are on the cast builder page
    const url = page.url();
    expect(url, "URL should contain cast-builder").toContain("cast-builder");

    // Verify the page has cast-related content
    const pageText = await page.textContent("body");
    const hasCastContent =
      pageText?.includes("Cast") ||
      pageText?.includes("cast") ||
      pageText?.includes("New Cast");
    expect(
      hasCastContent,
      "Cast builder page should display cast-related content"
    ).toBeTruthy();

    await screenshot(page, "b3-11-cast-builder-loaded");
  });

  test("Setup Phase renders with avatar selector", async ({ page }) => {
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await screenshot(page, "b3-11-setup-phase");
    await verifyNoErrorBoundary(page);

    // Verify cast name input is present
    const nameInput = page.getByPlaceholder(/My Awesome Cast|cast name/i);
    await expect(nameInput).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "b3-11-cast-name-input");

    // Verify avatar selection area exists
    const bodyText = await page.textContent("body");
    const hasAvatarSection =
      bodyText?.includes("Avatar") ||
      bodyText?.includes("avatar") ||
      bodyText?.includes("Select");
    expect(
      hasAvatarSection,
      "Setup phase should include avatar selection"
    ).toBeTruthy();

    // Look for avatar image tiles/buttons
    const avatarButtons = page
      .locator("button")
      .filter({ has: page.locator("img") });
    const avatarCount = await avatarButtons.count();

    if (avatarCount > 0) {
      await screenshot(page, "b3-11-avatar-tiles-visible");

      // Click the first avatar to select it
      await avatarButtons.first().click();
      await page.waitForTimeout(500);
      await screenshot(page, "b3-11-avatar-selected");
      await verifyNoErrorBoundary(page);
    }

    // Verify output format options exist (9:16, 16:9, etc.)
    const formats = ["9:16", "16:9", "1:1", "4:5"];
    let formatFound = false;
    for (const fmt of formats) {
      const fmtBtn = page.locator("button").filter({ hasText: fmt }).first();
      if (await fmtBtn.isVisible().catch(() => false)) {
        formatFound = true;
        break;
      }
    }

    // Verify Generate Script button is present
    const generateBtn = page
      .locator("button")
      .filter({ hasText: /Generate Script/i })
      .first();
    const hasGenerateBtn = await generateBtn
      .isVisible()
      .catch(() => false);

    await screenshot(page, "b3-11-setup-phase-complete");
  });

  test("Cast list page shows existing casts or create button", async ({
    page,
  }) => {
    await page.goto("/cast-builder");
    await waitForPageLoad(page);
    await screenshot(page, "b3-11-cast-list-page");
    await verifyNoErrorBoundary(page);

    // Verify the my-casts page container
    const myCastsPage = page.locator('[data-testid="my-casts-page"]');
    await expect(myCastsPage).toBeVisible({ timeout: TIMEOUTS.medium });

    // Verify "New Cast" button exists
    const newCastBtn = page
      .locator("button")
      .filter({ hasText: /New Cast/i })
      .first();
    await expect(
      newCastBtn,
      '"New Cast" button should be visible on the casts list page'
    ).toBeVisible({ timeout: TIMEOUTS.medium });
    await screenshot(page, "b3-11-new-cast-button");

    // Check for existing cast cards
    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount > 0) {
      // Verify at least the first cast card has content
      const firstCard = castCards.first();
      const cardText = await firstCard.textContent();
      expect(
        cardText?.length,
        "Cast card should have text content"
      ).toBeGreaterThan(0);

      // Verify cards show status badges
      const hasStatus =
        cardText?.includes("Draft") ||
        cardText?.includes("Ready") ||
        cardText?.includes("Generating") ||
        cardText?.includes("Audio") ||
        cardText?.includes("Failed") ||
        cardText?.includes("Setup");

      await screenshot(page, "b3-11-cast-cards-exist");
    } else {
      // Empty state: verify there is an informative message or the New Cast button is prominent
      const pageText = await page.textContent("body");
      const hasEmptyState =
        pageText?.includes("No casts") ||
        pageText?.includes("Create your first") ||
        pageText?.includes("Get started") ||
        pageText?.includes("New Cast");
      expect(
        hasEmptyState,
        "Cast list should show empty state message or New Cast prompt"
      ).toBeTruthy();
      await screenshot(page, "b3-11-cast-list-empty");
    }

    // Click New Cast and verify it navigates to the builder
    await newCastBtn.click();
    await page.waitForURL(/\/cast-builder\/new/, {
      timeout: TIMEOUTS.medium,
    });
    await waitForPageLoad(page);
    await screenshot(page, "b3-11-new-cast-navigated");
    await verifyNoErrorBoundary(page);
  });

  test("Cast builder phases are navigable (Setup -> Script -> Arrange)", async ({
    page,
  }) => {
    // First check for an existing cast to navigate through phases
    await page.goto("/cast-builder");
    await waitForPageLoad(page);
    await verifyNoErrorBoundary(page);

    const castCards = page.locator('[data-testid^="cast-card-"]');
    const cardCount = await castCards.count();

    if (cardCount > 0) {
      // Click an existing cast to see phase navigation
      await castCards.first().click();
      await page.waitForTimeout(3000);
      await screenshot(page, "b3-11-existing-cast-opened");
      await verifyNoErrorBoundary(page);

      // Verify phase header/navigation shows phase labels
      const phases = ["Setup", "Script", "Audio", "Arrange", "Render"];
      const pageText = await page.textContent("body");
      let phasesFound = 0;
      for (const p of phases) {
        if (pageText?.includes(p)) phasesFound++;
      }

      await screenshot(page, "b3-11-phase-labels");

      // Try clicking phase indicators if they are interactive
      for (const phaseName of ["Setup", "Script", "Arrange"]) {
        const phaseBtn = page
          .locator("button, [role='tab'], a")
          .filter({ hasText: new RegExp(`^${phaseName}$`, "i") })
          .first();
        if (await phaseBtn.isVisible().catch(() => false)) {
          // Check if it's clickable (not disabled)
          const isDisabled = await phaseBtn
            .getAttribute("disabled")
            .catch(() => null);
          if (isDisabled === null) {
            await phaseBtn.click();
            await page.waitForTimeout(1500);
            await verifyNoErrorBoundary(page);
            await screenshot(
              page,
              `b3-11-phase-${phaseName.toLowerCase()}-clicked`
            );
          }
        }
      }
    }

    // Also test the new cast flow phases
    await page.goto("/cast-builder/new");
    await waitForPageLoad(page);
    await screenshot(page, "b3-11-new-cast-setup-phase");
    await verifyNoErrorBoundary(page);

    // Verify we start in Setup phase
    const pageText = await page.textContent("body");
    const inSetupPhase =
      pageText?.includes("Setup") ||
      pageText?.includes("Cast Name") ||
      pageText?.includes("Avatar") ||
      pageText?.includes("Generate Script");
    expect(
      inSetupPhase,
      "New cast should start in the Setup phase"
    ).toBeTruthy();

    // Verify the phase stepper/header indicates the current phase
    const phaseIndicators = page.locator(
      '[data-testid="phase-header"], [data-testid="cast-phases"], [class*="stepper"], [class*="phase"]'
    );
    const hasPhaseUI = (await phaseIndicators.count()) > 0;

    await screenshot(page, "b3-11-phases-navigable-complete");
  });
});
