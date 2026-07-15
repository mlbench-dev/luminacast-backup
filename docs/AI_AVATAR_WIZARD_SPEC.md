# AI Avatar Creation Wizard — 4-Screen Specification

## Design Principles
- HeyGen: One decision per screen
- Synthesia: Clear step progress bar
- D-ID: Minimum viable inputs
- All: Preview before commit

## Screen 1: Describe Your Avatar (Free)
- "Surprise Me" button → OpenRouter LLM generates random character
- Fields: Name*, Appearance* (textarea 500ch), Selling Personality* (textarea 500ch)
- Dropdowns: Background, Camera, Visual Style
- Action: "Generate 4 Faces — $0.50"

## Screen 2: Pick Your Face ($0.50 per batch)
- 4 images from Gemini 3 Pro, same prompt, different seeds
- Select one, highlighted border
- "Tweak" text input + "Regenerate 4 ($0.50)"
- "Edit Details" goes back to Screen 1

## Screen 3: Choose Voice (Free)
- Tab: Browse voices / Clone a voice
- Lazy-loaded random Fish Audio voices, 6 at a time
- Filters: Gender, Language
- Same test sentence for all voices
- Shuffle button for new random set
- Clone: upload 10-30s audio

## Screen 4: Generate & Review ($9.49)
- Summary card with face thumbnail + voice + settings
- Pay remaining $9.49 (total $9.99 - $0.50)
- Progress: TTS audio → InfiniteTalk video
- Video player with test video (9:16 vertical)
- Approve / New Face ($0.50) / New Voice (free) / Redo Video ($0.99)

## Pricing
| Item | Cost to creator | Our cost |
|------|----------------|----------|
| Face batch (4 images) | $0.50 | ~$0.08 |
| Voice browse | Free | Free |
| Voice clone | Free | ~$0.05 |
| Video generation | $9.49 | ~$1.04 |
| **Total** | **$9.99** | **~$1.17** |
| Regenerate face | $0.50 | ~$0.08 |
| Redo video only | $0.99 | ~$0.25 |
