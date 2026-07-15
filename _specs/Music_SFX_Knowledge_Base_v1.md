# Music & SFX Knowledge Base v1.0
## Luminacast — Moods, Tags, SFX Library, Platform Rules

**Purpose:** This document is referenced by the AI when generating scripts (for SFX markers and prosody), by the Mubert service (for mood-to-tags mapping), and by the platform publishing logic (for music permission rules). It is designed to be edited by admins via `/preadmin`.

**Last updated:** April 2026

---

# SECTION 1 — Mood-to-Music Tags Mapping

When the AI generates a cast, the dominant mood determines the background music. Mubert API accepts tags to describe the desired music style.

## High Energy Moods

| Mood | Mubert Tags | When to use |
|---|---|---|
| `excited` | energetic, pop, upbeat, bright | Product reveals, unboxing, surprise moments |
| `urgent` | intense, driving, electronic, fast | CTAs, flash sales, countdown deals |
| `hype` | trap, bass, energetic, powerful | Challenge content, high-energy hooks |
| `triumphant` | epic, cinematic, powerful, uplifting | Before/after reveals, success stories |

## Medium Energy Moods

| Mood | Mubert Tags | When to use |
|---|---|---|
| `enthusiastic` | pop, upbeat, cheerful, positive | General product showcase, reviews |
| `confident` | corporate, motivational, modern, confident | Professional content, LinkedIn |
| `informative` | ambient, light, electronic, calm | Tutorials, educational, how-to |
| `trustworthy` | corporate, warm, acoustic, gentle | Social proof, testimonials |
| `playful` | fun, quirky, light, bouncy | Skit, comedy, challenge |

## Low Energy Moods

| Mood | Mubert Tags | When to use |
|---|---|---|
| `calm` | ambient, chill, relaxing, soft | ASMR, skincare routine, meditation |
| `intimate` | lofi, acoustic, intimate, warm | Storytime, personal confession, GRWM |
| `mysterious` | cinematic, dark, atmospheric, mysterious | Teaser, mystery reveal, myth busting |
| `emotional` | piano, emotional, cinematic, slow | Before/after "before", sad storytime |
| `dreamy` | ethereal, ambient, airy, gentle | Aesthetic content, room tour |

## Content-Type Defaults

| Content Type | Default Mood | Default Intensity | Default Tempo |
|---|---|---|---|
| product_showcase | enthusiastic | medium | medium |
| tutorial | informative | low | slow |
| before_after | emotional → triumphant | medium → high | slow → fast |
| flash_sale | urgent | high | fast |
| review | confident | medium | medium |
| storytime | intimate | low | slow |
| unboxing | excited | high | fast |
| live_selling | enthusiastic | medium | medium |
| GRWM | calm | low | slow |
| hot_take | confident | medium | fast |
| comparison | playful | medium | medium |

---

# SECTION 2 — SFX Library

Sound effects that the AI can insert into scripts via `[sfx:NAME]` markers. These are short audio clips (0.3-1.5 seconds) overlaid on top of the voice at specific moments.

## Available SFX

| Key | Icon | Sound | Duration | Use for |
|---|---|---|---|---|
| `whoosh` | 💨 | Swoosh/swipe transition | 0.5s | Transitions between blocks, scene changes |
| `pop` | 🫧 | Bubble pop / item appear | 0.3s | Text appearing, item reveal, list items |
| `ding` | 🔔 | Bell notification | 0.5s | Achievement, good news, feature highlight |
| `cash_register` | 💰 | Ka-ching register | 0.8s | Price reveal, purchase moment, deal |
| `sparkle` | ✨ | Magic shimmer | 0.7s | Product highlight, premium moment, reveal |
| `record_scratch` | 🔴 | Vinyl scratch stop | 0.6s | Pattern interrupt, "wait what?", correction |
| `swoosh_up` | 📈 | Rising whoosh | 0.5s | Energy building, excitement, improvement |
| `swoosh_down` | 📉 | Falling whoosh | 0.5s | Disappointment, "before" state, contrast |
| `notification` | 📳 | Phone notification | 0.4s | Social proof, "@user purchased", alert |
| `timer_tick` | ⏱ | Clock tick | 0.3s | Countdown, urgency, time pressure |
| `click` | 🖱 | Button click | 0.2s | CTA, "tap here", link press |
| `drumroll` | 🥁 | Short drumroll | 1.2s | Reveal anticipation, before result |
| `applause` | 👏 | Cheering/clapping | 1.5s | Celebration, social proof, milestone |
| `camera_shutter` | 📸 | Camera click | 0.3s | Photo moment, before/after capture |
| `bass_drop` | 🔊 | Deep bass hit | 0.5s | Major reveal, dramatic moment |
| `typing` | ⌨️ | Keyboard typing | 0.8s | Comment reading, text overlay |
| `coin` | 🪙 | Coin drop | 0.4s | Savings, discount, value mention |
| `success` | ✅ | Success chime | 0.5s | Task complete, benefit confirmed |

## SFX in Script — Rules for the AI

```
RULES:
- Max 2-3 SFX per 15-second block
- SFX enhance moments, they don't replace the voice
- Place [sfx:NAME] BEFORE the word it accompanies
- NEVER stack two SFX back-to-back
- Most common patterns:

  Hook: "[sfx:record_scratch] Wait, did you just say seven dollars?"
  Product reveal: "And THIS [sfx:sparkle] is what it looks like"
  Price reveal: "[sfx:cash_register] All of that for only $24.99"
  Social proof: "[sfx:notification] 47 people just added to cart"
  Transition: "[sfx:whoosh]" (alone, at block boundary)
  Countdown: "Only [sfx:timer_tick] three [sfx:timer_tick] hours left"
  CTA: "Tap the link [sfx:click] right now"
  Celebration: "[sfx:applause] Ten thousand five-star reviews"
```

---

# SECTION 3 — Prosody Tags (Fish Speech S2)

Prosody tags control HOW the avatar speaks — energy, emotion, pacing. Fish Speech S2 processes these natively within the text.

## Available Prosody Tags

| Tag | Effect | Example use |
|---|---|---|
| `(excited)` | High energy, enthusiasm | Product reveals, CTAs, unboxing |
| `(casual)` | Relaxed, conversational | General TikTok, GRWM, chill content |
| `(whispering)` | Soft, intimate, ASMR | Secrets, insider tips, "nobody talks about this" |
| `(laughing)` | Natural laugh mid-speech | Reactions, relatability, humor |
| `(sighing)` | Exhale, frustration | Problem hooks, "I'm so tired of..." |
| `(super happy)` | Over-the-top joy | Purchase celebrations, big reveals |
| `(sad)` | Downbeat, empathy | Before state, problem acknowledgment |
| `(angry)` | Intensity, conviction | Hot takes, myth busting, "this is wrong" |
| `[pause]` | 0.5-1s dramatic pause | After hooks, before reveals, emphasis |

## Prosody in Script — Rules for the AI

```
RULES:
- Max 3-4 prosody tags per 15-second block
- NEVER start a block with a tag — start with words, tag comes mid-flow
- [pause] is the most powerful tool — use after the hook line
- (casual) is default TikTok energy — use to reset after high moments
- Do NOT overuse — subtlety > saturation

GOOD:
"Oh my god you guys, (excited) this GOPURE neck cream? [pause]
Like, it's been tested on over ten THOUSAND women.
(whispering) And honestly? The results are insane."

BAD (overtagged):
"(excited) OH WOW! (super happy) This is AMAZING! (laughing) Ha ha!
(whispering) You need this! (excited) BUY NOW!"
```

---

# SECTION 4 — Platform Music Permission Rules

The system checks these rules when publishing or going live to warn users about music restrictions.

```
PLATFORM RULES (as of April 2026):

TikTok (recorded):
  ✅ Mubert AI-generated music — fully safe
  ✅ TikTok Sounds library — for personal/creator use
  ⚠️ Commercial Music Library (CML) ONLY for Shop/commercial
  ❌ No copyrighted music in Shop/affiliate videos without CML

TikTok (LIVE):
  ✅ Mubert AI-generated music — fully safe, RECOMMENDED
  ✅ TikTok Live Sound Library
  ❌ ALL copyrighted music BANNED (since July 25, 2025)
  ❌ No Spotify, Apple Music, YouTube music playing in background
  NOTE: "Mubert-powered music is a selling point — our users get
        legal background music while others stream in silence"

YouTube (recorded):
  ✅ Mubert AI-generated — safe, no Content ID flags
  ⚠️ Copyrighted music triggers Content ID claims
  BEST PRACTICE: Always use Mubert for YouTube to avoid demonetization

YouTube (LIVE):
  ✅ Mubert AI-generated — safe
  ❌ Copyrighted music can trigger live stream termination

Instagram (recorded):
  ✅ Mubert — safe
  ✅ IG Music library — for personal use
  ⚠️ Commercial accounts should use royalty-free

Instagram (LIVE):
  ✅ Mubert — safe
  ❌ Copyrighted music — increasingly restricted

Facebook (recorded + LIVE):
  ✅ Mubert — safe
  ❌ Copyrighted — risks muting/takedown

LinkedIn (recorded):
  ✅ Mubert — safe
  ⚠️ RECOMMENDATION: Professional content often performs BETTER 
     without background music. Suggest removing for LinkedIn.

Pinterest:
  ✅ Mubert — safe
  Music is less important — Pinterest is primarily visual/text
```

---

# SECTION 5 — Music Generation Catalog Presets

Pre-generated music tracks across common moods. Refreshed weekly via batch Celery task. Users can browse these instantly without waiting for Mubert generation.

| Preset Name | Mood | Intensity | Tempo | Durations |
|---|---|---|---|---|
| Energetic Pop | excited | high | fast | 30s, 60s, 90s |
| Chill Lo-fi | calm | low | slow | 30s, 60s, 90s |
| Corporate Motivational | confident | medium | medium | 30s, 60s, 90s |
| Dramatic Cinematic | mysterious | high | medium | 30s, 60s, 90s |
| Upbeat Electronic | hype | high | fast | 30s, 60s, 90s |
| Warm Acoustic | trustworthy | low | slow | 30s, 60s, 90s |
| Intense Driving | urgent | high | fast | 30s, 60s, 90s |
| Ambient Minimal | informative | low | slow | 30s, 60s, 90s |
| Fun Quirky | playful | medium | fast | 30s, 60s, 90s |
| Dreamy Ethereal | dreamy | low | slow | 30s, 60s, 90s |

= 10 presets × 3 durations = 30 pre-generated tracks in the catalog.

---

*This document is version 1.0. Update when: new SFX are added, Mubert tags change, platform music rules update, or new prosody tags become available in Fish Speech.*
