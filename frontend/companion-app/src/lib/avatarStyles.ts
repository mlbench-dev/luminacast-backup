/**
 * Avatar style presets — 12 items, displayed as 3x4 grid in SetupPhase.
 * Each preset maps to a prompt fragment injected into the identity generation prompt.
 */
export const STYLE_PRESETS = [
  { id: "studio_pro", label: "Studio Pro", subtitle: "Polished, flawless skin", icon: "camera" },
  { id: "natural_real", label: "Natural / Real", subtitle: "Realistic, imperfect", icon: "user" },
  { id: "cinematic", label: "Cinematic", subtitle: "Film-style, dramatic lighting", icon: "film" },
  { id: "stylized_cartoon", label: "Stylized / Cartoon", subtitle: "Illustrated, animated", icon: "palette" },
  { id: "street", label: "Street", subtitle: "Candid, raw urban", icon: "map-pin" },
  { id: "glamour", label: "Glamour", subtitle: "High-fashion, bold", icon: "sparkles" },
  { id: "editorial", label: "Editorial", subtitle: "Magazine spread, soft light", icon: "book-open" },
  { id: "golden_hour", label: "Golden Hour", subtitle: "Warm sunset, dreamy", icon: "sun" },
  { id: "anime", label: "Anime", subtitle: "Japanese animation", icon: "heart" },
  { id: "cyberpunk", label: "Cyberpunk", subtitle: "Neon, futuristic", icon: "zap" },
  { id: "vintage_film", label: "Vintage Film", subtitle: "Grainy, 70s-90s", icon: "aperture" },
  { id: "soft_beauty", label: "Soft Beauty", subtitle: "Dewy, K-beauty", icon: "flower" },
] as const;

export type StylePresetId = (typeof STYLE_PRESETS)[number]["id"];

/**
 * Prompt fragments keyed by preset ID.
 * Injected into avatar_name_and_description and flux_face_portrait prompts.
 */
export const STYLE_PROMPT_FRAGMENTS: Record<StylePresetId, string> = {
  studio_pro: "professional studio lighting, flawless polished skin, clean white background, magazine-quality portrait",
  natural_real: "natural daylight, realistic skin texture with pores, candid amateur photograph, authentic imperfect lighting",
  cinematic: "cinematic film still, dramatic rim lighting, shallow depth of field, anamorphic lens, moody color grading",
  stylized_cartoon: "anime art style, Japanese animation, vibrant colors, cel-shaded, digital illustration",
  street: "urban street photography, candid raw aesthetic, gritty textures, bokeh city background, editorial streetwear",
  glamour: "high-fashion glamour photography, bold makeup, dramatic contouring, luxury lighting, glossy magazine",
  editorial: "editorial magazine spread, soft diffused lighting, neutral tones, minimal background, fashion-forward",
  golden_hour: "golden hour warm sunset lighting, dreamy soft glow, warm amber tones, outdoor romantic",
  anime: "anime art style, Japanese animation, vibrant saturated colors, cel-shaded, large expressive eyes",
  cyberpunk: "neon-lit cyberpunk aesthetic, futuristic, holographic accents, dark urban backdrop with neon reflections",
  vintage_film: "vintage film grain, 70s-90s film photography, warm desaturated tones, retro color palette, analog feel",
  soft_beauty: "dewy K-beauty aesthetic, glass skin, soft pastel tones, gentle diffused lighting, luminous glow",
};

/**
 * "Make it real" chips — 16 items for adding human details to the avatar.
 * Each chip has a label (shown in UI) and a prompt fragment (injected into description).
 */
export const MAKE_IT_REAL_CHIPS = [
  { id: "phone_selfie", label: "Phone selfie", prompt: "shot on smartphone camera, slight grain, natural uneven lighting" },
  { id: "tired_after_work", label: "Tired after work", prompt: "slightly fatigued face, subtle under-eye circles, end-of-day look" },
  { id: "bare_face", label: "Bare face", prompt: "zero makeup, natural bare skin, visible pores, authentic everyday appearance" },
  { id: "outdoor_light", label: "Outdoor light", prompt: "natural daylight outdoors, dappled sunlight, environmental ambient light" },
  { id: "laugh_lines", label: "Laugh lines", prompt: "visible smile lines and crow's feet from years of genuine smiling" },
  { id: "morning_look", label: "Morning look", prompt: "just woke up energy, slightly disheveled hair, cozy and approachable" },
  { id: "weathered", label: "Weathered", prompt: "sun-kissed skin with texture, freckles, slight tan lines, lived outdoors" },
  { id: "office_casual", label: "Office casual", prompt: "slightly loosened collar, rolled-up sleeves, professional but human" },
  { id: "asymmetric", label: "Asymmetric", prompt: "natural facial asymmetry, one eyebrow slightly higher, non-symmetrical features" },
  { id: "freckles", label: "Freckles", prompt: "light natural freckles across the nose and cheeks" },
  { id: "skin_texture", label: "Skin texture", prompt: "visible skin texture and natural pores, not airbrushed" },
  { id: "bushy_brows", label: "Bushy brows", prompt: "naturally full, slightly unruly eyebrows" },
  { id: "gap_teeth", label: "Gap teeth", prompt: "small charming gap between front teeth" },
  { id: "natural_pores", label: "Natural pores", prompt: "visible natural skin pores, unretouched complexion" },
  { id: "stray_hairs", label: "Stray hairs", prompt: "a few stray hairs, not perfectly styled, natural and lived-in" },
  { id: "post_workout", label: "Post-workout glow", prompt: "slight sheen of sweat, flushed healthy complexion, post-exercise glow" },
] as const;

export type MakeItRealChipId = (typeof MAKE_IT_REAL_CHIPS)[number]["id"];
