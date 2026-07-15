/**
 * Estimated render times per pipeline step.
 * Starting values based on observed production timings — refine over time.
 */
export const RENDER_ESTIMATES: Record<string, { fixed?: number; perSecond?: number; perChar?: number; base?: number }> = {
  // Voice / audio
  bs_roformer_separate: { perSecond: 0.5, base: 5 },
  fish_speech_tts: { perChar: 0.05, base: 3 },
  whisper_transcribe: { perSecond: 0.1, base: 2 },

  // Face / image
  flux_face_candidates: { fixed: 25 },
  flux_background: { fixed: 12 },
  flux_body_motion: { fixed: 15 },
  kling_tryon: { fixed: 30 },

  // Video
  infinitetalk_lipsync: { perSecond: 4.5, base: 10 },
  musetalk_lipsync: { perSecond: 1.2, base: 5 },
  wan_body_motion: { perSecond: 8, base: 15 },
  kling_lipsync: { perSecond: 3, base: 8 },

  // Compositor
  ffmpeg_compose: { perSecond: 0.8, base: 5 },
};

export function estimateRenderSeconds(step: string, params?: { durationSeconds?: number; charCount?: number }): number {
  const est = RENDER_ESTIMATES[step];
  if (!est) return 30;
  if (est.fixed != null) return est.fixed;
  if (est.perSecond != null) return (est.base || 0) + est.perSecond * (params?.durationSeconds || 0);
  if (est.perChar != null) return (est.base || 0) + est.perChar * (params?.charCount || 0);
  return 30;
}
