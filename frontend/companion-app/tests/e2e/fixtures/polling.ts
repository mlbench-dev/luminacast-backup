import { Page, expect } from "@playwright/test";

export interface PollOptions {
  /** Maximum time in ms to wait for condition */
  timeout?: number;
  /** Interval between polls in ms */
  interval?: number;
  /** Description for error messages */
  label?: string;
}

/**
 * Poll a condition until it returns true or timeout.
 * Used for long-running async operations like video rendering.
 */
export async function pollUntil(
  fn: () => Promise<boolean>,
  opts: PollOptions = {}
): Promise<void> {
  const { timeout = 300_000, interval = 5_000, label = "condition" } = opts;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error(`pollUntil timed out after ${timeout}ms waiting for: ${label}`);
}

/**
 * Poll a cast status until it reaches the target state.
 * Fetches status by navigating to the cast page and reading the status badge.
 */
export async function pollCastStatus(
  page: Page,
  castId: string,
  targetStatus: string | RegExp,
  opts: PollOptions = {}
): Promise<void> {
  const { timeout = 600_000, interval = 10_000, label } = opts;
  const desc = label || `cast ${castId} → ${targetStatus}`;
  await pollUntil(
    async () => {
      await page.reload({ waitUntil: "networkidle" });
      const statusBadge = page.locator(
        '[data-testid="cast-status"], [data-testid="cast-status-badge"]'
      );
      if (!(await statusBadge.isVisible({ timeout: 5000 }).catch(() => false))) {
        return false;
      }
      const text = await statusBadge.textContent();
      if (typeof targetStatus === "string") {
        return text?.toLowerCase().includes(targetStatus.toLowerCase()) ?? false;
      }
      return targetStatus.test(text || "");
    },
    { timeout, interval, label: desc }
  );
}

/**
 * Poll an API endpoint until a condition is met.
 * Useful for checking render status via backend API.
 */
export async function pollApiEndpoint(
  page: Page,
  url: string,
  conditionFn: (data: any) => boolean,
  opts: PollOptions = {}
): Promise<any> {
  const { timeout = 600_000, interval = 10_000, label = url } = opts;
  const deadline = Date.now() + timeout;
  let lastData: any = null;
  while (Date.now() < deadline) {
    try {
      const response = await page.request.get(url);
      if (response.ok()) {
        lastData = await response.json();
        if (conditionFn(lastData)) return lastData;
      }
    } catch {
      // API might be temporarily unavailable, keep polling
    }
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error(
    `pollApiEndpoint timed out after ${timeout}ms for: ${label}. Last data: ${JSON.stringify(lastData)?.slice(0, 200)}`
  );
}

/**
 * Wait for a download to complete and return the download path.
 */
export async function waitForDownload(
  page: Page,
  triggerFn: () => Promise<void>,
  opts: { timeout?: number } = {}
): Promise<string> {
  const { timeout = 120_000 } = opts;
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout }),
    triggerFn(),
  ]);
  const filePath = await download.path();
  if (!filePath) throw new Error("Download completed but no file path available");
  return filePath;
}
