import { Page, expect } from "@playwright/test";

export interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
  message?: string;
}

export async function pollUntil(
  fn: () => Promise<boolean>,
  opts: PollOptions = {}
): Promise<void> {
  const { timeoutMs = 300_000, intervalMs = 5_000, message = "condition" } = opts;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`pollUntil timed out after ${timeoutMs}ms waiting for: ${message}`);
}

export async function pollApiEndpoint(
  page: Page,
  url: string,
  conditionFn: (data: any) => boolean,
  opts: PollOptions = {}
): Promise<any> {
  const { timeoutMs = 600_000, intervalMs = 10_000, message = url } = opts;
  const deadline = Date.now() + timeoutMs;
  let lastData: any = null;
  let consecutiveErrors = 0;
  while (Date.now() < deadline) {
    try {
      const response = await page.request.get(url);
      if (response.ok()) {
        lastData = await response.json();
        consecutiveErrors = 0;
        if (conditionFn(lastData)) return lastData;
      } else {
        consecutiveErrors++;
        console.warn(`[pollApiEndpoint] ${url}: HTTP ${response.status()} (${consecutiveErrors} consecutive errors)`);
      }
    } catch (err: any) {
      consecutiveErrors++;
      console.warn(`[pollApiEndpoint] ${url}: ${err.message} (${consecutiveErrors} consecutive errors)`);
    }
    if (consecutiveErrors >= 5) {
      throw new Error(`pollApiEndpoint: 5 consecutive errors polling ${url}. Last data: ${JSON.stringify(lastData)?.slice(0, 300)}`);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(
    `pollApiEndpoint timed out after ${timeoutMs}ms for: ${message}. Last: ${JSON.stringify(lastData)?.slice(0, 300)}`
  );
}

/**
 * Assert that a render test took at least `minMs` milliseconds.
 * If a render test finishes in under 2 minutes, something bypassed the actual render.
 */
export function assertMinRuntime(startMs: number, minMs: number = 120_000): void {
  const elapsed = Date.now() - startMs;
  expect(
    elapsed,
    `Render test finished in ${Math.round(elapsed / 1000)}s — expected at least ${Math.round(minMs / 1000)}s. Something skipped the real render.`
  ).toBeGreaterThanOrEqual(minMs);
}
