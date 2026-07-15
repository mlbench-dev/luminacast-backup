import { Page, TestInfo } from "@playwright/test";
import * as path from "path";

/**
 * Capture a full-page screenshot of the editor for visual regression review.
 * Saves to test-results/ and attaches to the test report.
 */
export async function captureEditorScreenshot(
  page: Page,
  testInfo: TestInfo,
  label: string = "editor"
): Promise<string> {
  const timestamp = Date.now();
  const fileName = `gauntlet-${label}-${timestamp}.png`;
  const filePath = path.join(process.cwd(), "test-results", fileName);

  // Wait for editor to be fully loaded
  const editorShell = page.locator('[data-testid="editor-shell"]');
  await editorShell.waitFor({ state: "visible", timeout: 30000 });

  // Take screenshot
  const buffer = await page.screenshot({ fullPage: true });
  if (!buffer || buffer.length === 0) {
    throw new Error("Editor screenshot is empty — editor may not have loaded");
  }

  // Write to disk
  const fs = await import("fs");
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(filePath, buffer);

  // Attach to test report
  await testInfo.attach(fileName, {
    path: filePath,
    contentType: "image/png",
  });

  return filePath;
}
