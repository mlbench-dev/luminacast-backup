# Generated Videos — Shipment A Summary

Date: 2026-04-14

## Files Added
| File | Purpose |
|------|---------|
| `kling_wan_prompting_guide.md` | Engine-specific prompting guide for Inspire Me LLM |
| `backend/orchestrator/services/kling_tti2v_client.py` | Kling v2.1 Master T2V + Pro I2V client |
| `backend/orchestrator/services/wan_tti2v_client.py` | Wan 2.2 A14B T2V + 5B I2V client |
| `backend/orchestrator/services/generated_videos_service.py` | Video generation orchestrator + R2 upload |
| `backend/orchestrator/services/inspire_me_service.py` | Engine-aware prompt enhancement |
| `backend/orchestrator/routers/generated_videos.py` | REST endpoints: generate/list/delete/inspire-me |
| `backend/orchestrator/models/ai_generated_video.py` | SQLAlchemy model for ai_generated_videos |
| `backend/orchestrator/migrations/versions/gv01_create_ai_generated_videos.py` | Alembic migration |
| `frontend/.../VideosSubfolderSelector.tsx` | Uploaded/Generated pill row |
| `frontend/.../GeneratedVideosGrid.tsx` | AI video grid with detail drawer |
| `frontend/.../GenerateVideoWithAIModal.tsx` | Full generation modal |

## Files Modified (additive only)
| File | Change |
|------|--------|
| `backend/orchestrator/services/ai_prompts.py` | Added `generated_video_inspire_me` prompt entry |
| `backend/orchestrator/models/__init__.py` | Added `AIGeneratedVideo` import + export |
| `backend/orchestrator/main.py` | Added `generated_videos` router import + include |
| `frontend/.../pages/MyVideos.tsx` | Added Videos subfolder selector + generated grid wiring |

## Files NOT Touched (scope discipline)
- `cast-builder/**` — untouched
- `tasks/generate_cast.py` — untouched
- `routers/avatar.py` — untouched
- `routers/casts.py` — untouched
- `services/kling_lipsync.py` — untouched (avatar acting video pipeline)
- All body-shots-related files — untouched
- All photos feature files — untouched (used as reference only)
- Product Library, Go Live, Music, Analytics — untouched

## Open Items for Future
1. **Shipment B (sequence chaining)** — shot 2 continues from shot 1's last frame. Architecture: last-frame extraction → continuity prompting → ffmpeg concat. Not started.
2. **Thumbnail generation** — currently shows full video element; could optimize with ffmpeg-extracted thumbnails for faster grid loading.
3. **`generation_costs` persistence** — cost is ephemeral today (returned in API response, logged). Deferred to consolidation instruction.
4. **Kling v2.1 Master tier** — higher quality, higher cost. Could be added as a quality tier option.
5. **Style presets** — curated prompt fragments (cinematic, documentary, anime) for one-click style selection.
6. **Advanced camera control** — multi-axis, keyframed camera paths (when Kling exposes richer controls).
7. **Reference image picker sources** — currently supports file upload only. Could add "My Photos" and "AI Generated" tabs fetching from existing APIs.

## API Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/videos/generate` | Generate 1-4 videos with Kling or Wan |
| GET | `/api/videos/generated` | List user's generated videos (paginated) |
| DELETE | `/api/videos/generated/{id}` | Soft-delete a generated video |
| POST | `/api/videos/inspire-me` | Engine-aware prompt enhancement |

## Database
- Table: `ai_generated_videos` with soft-delete (`deleted_at`)
- R2 prefix: `users/{user_id}/generated_videos/`
- No FK to any existing video/cast/render table (scoped isolation)
