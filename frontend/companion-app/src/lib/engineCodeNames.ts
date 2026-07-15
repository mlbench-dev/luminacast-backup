/**
 * Code names for third-party engines/services.
 * User-facing UI strings must use these names instead of real vendor names.
 * Internal logs, comments, imports, and Sentry context keep real names.
 */
export const ENGINE_CODE_NAMES: Record<string, string> = {
  // Lipsync
  infinitetalk: "Studio Render",
  musetalk: "Live Render",

  // TTS
  elevenlabs: "Voice Studio",
  fish_audio: "Voice Foundry",
  fish_speech: "Voice Foundry",

  // Image gen
  flux_kontext: "Vision Engine",
  flux: "Vision Engine",

  // Try-on / virtual fitting
  kling_kolors: "Wardrobe Engine",
  kling_lipsync: "Motion Sync",

  // Music
  ace_step: "Sound Forge",

  // Video gen
  wan_body_motion: "Motion Engine",

  // Audio processing
  bs_roformer: "Audio Isolator",
  whisper: "Transcription Engine",
  pyannote: "Speaker Engine",

  // Compute
  runpod: "GPU Cloud",
};

/**
 * Replace a vendor engine name with its code name.
 * Falls back to the original name if no mapping exists.
 */
export function codeNameFor(engine: string): string {
  const key = engine.toLowerCase().replace(/[\s-]/g, "_");
  return ENGINE_CODE_NAMES[key] || engine;
}
