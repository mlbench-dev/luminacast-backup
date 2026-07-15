# Kling v2.1 + Wan 2.2 Prompting Guide

Reference guide for the Inspire Me LLM to craft engine-optimized video generation prompts.

---

## Kling v2.1 (Master T2V / Pro I2V)

### Strengths
Kling excels at cinematic realism, character consistency, and precise camera motion. It handles close-ups, medium shots, and wide establishing shots with film-grade quality. Lighting responsiveness and color grading are strong suits.

### Prompt Structure
Lead with the **subject and action**, then **setting**, then **cinematic modifiers**. Kling responds well to film terminology.

**Effective descriptors:**
- Shot sizes: `close-up`, `medium shot`, `wide establishing shot`, `aerial shot`, `over-the-shoulder`
- Lens language: `35mm lens`, `anamorphic`, `shallow depth of field`, `rack focus`
- Lighting: `golden hour`, `studio lighting`, `neon-lit`, `chiaroscuro`, `backlit silhouette`
- Film grain & color: `Kodak Portra 400`, `desaturated teal-orange`, `warm color grade`, `high contrast`
- Style keywords: `cinematic`, `documentary`, `photorealistic`, `film noir`, `slow motion`

### Camera Motion
When using Kling T2V with camera control presets (`down_back`, `forward_up`, `right_turn_forward`, `left_turn_forward`), reinforce the motion direction in the prompt text. Example: if `forward_up` is selected, include "camera slowly rises and pushes forward" in the prompt.

### Duration Pacing
- **5 seconds:** One focused action or reveal. Keep it simple — a single subject performing one clear motion. "A woman turns to face the camera and smiles."
- **10 seconds:** Room for a two-beat sequence. Start with an establishing moment, transition to the action. "Rain falls on a cobblestone street. A figure in a red coat opens an umbrella and walks toward the camera."

### What Kling Struggles With
- Long text on signs or screens — often garbled
- Very fast or complex multi-limb motion (breakdancing, juggling)
- Detailed hand movements and finger counting
- More than 2-3 characters interacting simultaneously
- Abstract non-representational content

### Example Prompts
- **Close-up:** "Extreme close-up of an eye reflecting city lights, pupil dilating slowly, warm amber tones, shallow depth of field, anamorphic lens flare"
- **Medium shot:** "A barista in a blue apron pours steaming milk into a latte, creating delicate rosetta art, warm café lighting, 35mm film grain, soft bokeh in background"
- **Wide shot:** "Wide establishing shot of a misty mountain valley at dawn, first rays of sunlight breaking through clouds, aerial perspective slowly descending, cinematic color grade"
- **With camera:** "City skyline at dusk with twinkling lights, camera slowly pulls back and tilts down to reveal a rooftop garden, golden hour lighting"

---

## Wan 2.2 (A14B T2V / 5B I2V)

### Strengths
Wan produces vivid, high-motion videos with strong color saturation and dynamic movement. It handles natural scenes, fluid motion, and stylized content well. Wan is prompt-driven for all camera motion — there are no explicit camera control parameters.

### Prompt Structure
Be concrete and specific. Wan responds to **action verbs** and **explicit scene composition** rather than abstract film terminology. Structure: **subject doing action** + **setting details** + **style/mood**.

**Effective descriptors:**
- Action verbs: `runs`, `dances`, `explodes`, `flows`, `transforms`, `melts`, `blooms`
- Scene composition: `foreground`, `background`, `center frame`, `fills the frame`
- Style keywords: `photorealistic`, `anime style`, `watercolor`, `oil painting`, `3D render`, `claymation`
- Lighting moods: `sunset glow`, `moonlight`, `neon cityscape`, `soft diffused light`, `dramatic shadows`
- Motion descriptors: `slow motion`, `time-lapse`, `fast-paced`, `smooth flowing`

### Camera Motion via Prompt
Since Wan has no camera control API, describe camera movement explicitly:
- `"camera slowly dollies forward"`, `"slow pan from left to right"`, `"camera tilts up to reveal the sky"`
- `"bird's eye view descending"`, `"tracking shot following the subject"`, `"static wide angle"`
- Keep camera instructions simple — one direction, one speed. Complex multi-axis moves degrade quality.

### Duration Pacing
- **5 seconds (~81 frames):** Single scene, one clear action. Best for loops, product reveals, simple motions.
- **10 seconds (~161 frames):** Can handle scene transitions but risks coherence loss. Keep the narrative thread simple.

### What Wan Struggles With
- Long-duration coherence (>7s often drifts)
- Dialogue or lip sync (no audio alignment)
- Complex character interactions (>2 subjects touching)
- Very specific text or logos
- Maintaining identity of a specific person across the clip

### Example Prompts
- **Nature scene:** "Turquoise ocean waves crash against volcanic black rocks, white spray erupts upward, camera slowly pulls back to reveal a tropical coastline, warm sunset colors"
- **Product motion:** "A glass perfume bottle rotates slowly on a reflective black surface, golden light catches the faceted edges, soft bokeh sparkles in the background"
- **Stylized:** "A fox made of autumn leaves runs through a misty forest, leaves scatter behind it, magical golden particles trail in its wake, fantasy art style"
- **Time-lapse:** "Time-lapse of a red rose blooming from tight bud to full flower, soft studio lighting, macro lens, black background, 10 seconds"

---

## Common Pitfalls (Both Engines)

1. **Too-long prompts become noise.** Keep to 1-3 sentences. Beyond that, later tokens get ignored or cause conflicting instructions.
2. **Conflicting style keywords** degrade output. Don't combine "realistic" with "anime" or "documentary" with "fantasy." Pick one aesthetic.
3. **Ambiguous subjects** produce generic results. "A man" → "A bearded 40-year-old man in a charcoal wool coat" is dramatically better.
4. **Negative prompts** work differently per engine. Kling respects them for quality control (`blur, distort, low quality`). Wan's negative prompt has milder effect — rely on positive phrasing instead.
5. **Image-to-video prompts** should describe **motion**, not the image content. The image already provides the subject — the prompt should say what happens next. Bad: "a dog sitting on grass." Good: "the dog leaps up and runs toward the camera, tail wagging."
6. **Overly specific timing** ("at the 3-second mark, the camera pans") doesn't work. These models don't understand temporal keyframes.
