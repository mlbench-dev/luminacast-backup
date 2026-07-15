import { Page, Locator, expect } from "@playwright/test";

export async function clickPlayAndVerify(
  page: Page,
  videoSelector: string | Locator,
  opts: { timeout?: number; clickOverlay?: string } = {}
): Promise<void> {
  const { timeout = 10000, clickOverlay } = opts;

  const video =
    typeof videoSelector === "string"
      ? page.locator(videoSelector)
      : videoSelector;

  await expect(video).toBeVisible({ timeout });

  // Try clicking overlay first if provided, otherwise click the video
  if (clickOverlay) {
    const overlay = page.locator(clickOverlay);
    if (await overlay.isVisible({ timeout: 3000 }).catch(() => false)) {
      await overlay.click();
    } else {
      await video.click();
    }
  } else {
    await video.click();
  }

  // Wait for play to start
  await page.waitForTimeout(2000);

  // Assert video.paused === false
  const isPaused = await video.evaluate((v: HTMLVideoElement) => v.paused);
  expect(isPaused, "Video should be playing after click (video.paused === false)").toBe(false);
}

export async function clickPlayAndVerifyAudio(
  page: Page,
  audioSelector: string | Locator,
  opts: { timeout?: number } = {}
): Promise<void> {
  const { timeout = 10000 } = opts;
  const audio =
    typeof audioSelector === "string"
      ? page.locator(audioSelector)
      : audioSelector;

  await expect(audio).toBeVisible({ timeout });
  await audio.evaluate((el: HTMLAudioElement) => el.play());
  await page.waitForTimeout(1000);
  const isPaused = await audio.evaluate((el: HTMLAudioElement) => el.paused);
  expect(isPaused, "Audio should be playing (audio.paused === false)").toBe(false);
}
