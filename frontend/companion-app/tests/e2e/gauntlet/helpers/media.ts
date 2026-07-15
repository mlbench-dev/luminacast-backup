import { APIRequestContext, TestInfo } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

/**
 * Download an MP4 from `url`, verify ftyp magic bytes, run ffprobe,
 * assert a video stream exists with a sane duration, attach to report.
 *
 * Throws on any failure — no silent fallbacks.
 */
export async function downloadAndVerifyMP4(
  request: APIRequestContext,
  url: string,
  filePath: string,
  testInfo?: TestInfo
): Promise<number> {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const response = await request.get(url);
  if (!response.ok()) {
    throw new Error(`Download failed: ${response.status()} ${response.statusText()} for ${url}`);
  }
  const buffer = await response.body();
  fs.writeFileSync(filePath, buffer);

  // ftyp magic byte check (offset 4-8)
  if (buffer.length < 8) throw new Error("MP4 file too small");
  const ftyp = buffer.slice(4, 8).toString("ascii");
  if (ftyp !== "ftyp") throw new Error(`Invalid MP4: expected ftyp at offset 4, got "${ftyp}"`);

  // ffprobe duration check — REQUIRED, no silent skip
  const { execSync } = require("child_process");
  const ffprobeOut = execSync(
    `ffprobe -v quiet -print_format json -show_streams "${filePath}"`,
    { timeout: 30000 }
  ).toString();
  const probe = JSON.parse(ffprobeOut);
  const videoStream = probe.streams?.find((s: any) => s.codec_type === "video");
  if (!videoStream) throw new Error("MP4 has no video stream");
  const duration = parseFloat(videoStream.duration || "0");
  if (duration < 5 || duration > 600) {
    throw new Error(`MP4 duration ${duration}s outside valid range 5-600s`);
  }

  // Attach as test artifact — REQUIRED
  if (!testInfo) throw new Error("testInfo is required — every MP4 must be attached to the report");
  await testInfo.attach(path.basename(filePath), {
    path: filePath,
    contentType: "video/mp4",
  });

  return duration;
}

/**
 * Download an audio file, run ffprobe, assert an audio stream exists.
 *
 * Throws on any failure — no silent fallbacks.
 */
export async function downloadAndVerifyAudio(
  request: APIRequestContext,
  url: string,
  filePath: string,
  testInfo?: TestInfo
): Promise<number> {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const response = await request.get(url);
  if (!response.ok()) {
    throw new Error(`Audio download failed: ${response.status()} for ${url}`);
  }
  const buffer = await response.body();
  fs.writeFileSync(filePath, buffer);
  if (buffer.length < 100) throw new Error("Audio file too small");

  // ffprobe — REQUIRED, no silent skip
  const { execSync } = require("child_process");
  const ffprobeOut = execSync(
    `ffprobe -v quiet -print_format json -show_streams "${filePath}"`,
    { timeout: 30000 }
  ).toString();
  const probe = JSON.parse(ffprobeOut);
  const audioStream = probe.streams?.find((s: any) => s.codec_type === "audio");
  if (!audioStream) throw new Error("File has no audio stream");
  const duration = parseFloat(audioStream.duration || "0");

  if (!testInfo) throw new Error("testInfo is required — every audio file must be attached to the report");
  await testInfo.attach(path.basename(filePath), {
    path: filePath,
    contentType: "audio/mpeg",
  });

  return duration;
}
