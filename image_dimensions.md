# Image Dimensions — Avatar Pipeline

## Face Shot (FLUX Kontext Pro)
- **Generate at**: 1024x1024 (FLUX native square for portraits)
- **Store original**: Full quality JPEG in R2 at `creators/{uid}/avatar/{aid}/face_ref.jpg`
- **InfiniteTalk consumption**: Auto-resized by InfiniteTalk pipeline — no manual resize needed
- **InfiniteTalk accepts**: Any reasonable portrait image. The GPU worker downloads and saves as PNG internally.

## Body Shots (FLUX Kontext Pro)
- **Generate at**: 720x1280 vertical (9:16 aspect ratio)
- **FLUX aspect_ratio param**: `"9:16"`
- **Store original**: Full quality JPEG in R2 at `creators/{uid}/avatar/{aid}/body_shots/{set_id}/{angle}.jpg`
- **Angles**: front, three_quarter_left, three_quarter_right, profile_left, profile_right, back

## InfiniteTalk Video Output
- **Size presets**:
  - `480p`: 480x854 (9:16)
  - `720p`: 720x1280 (9:16)
  - `1080p`: 1080x1920 (9:16)
- **Default for previews**: 480p (fastest render, sufficient for preview)
- **Default for production**: 720p (cast generation)
- **Input face image**: No strict dimension requirement; GPU worker downloads and converts internally

## General Rules
- Always store the highest-quality original in R2
- Downstream consumers (InfiniteTalk, frontend thumbnails) resize on demand
- R2 keys never contain full URLs — CDN domain appended at API serialization layer
- Face images stored as JPEG; video as MP4 with faststart flag
