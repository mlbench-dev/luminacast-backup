# Avatar System Overhaul — Implementation Spec

## Root Issues Found
1. **Celery tasks never execute** — `autodiscover_tasks` was not finding the task modules. Tasks get queued but never processed. Already fixed in `tasks/__init__.py` by using explicit `include=[]`.
2. **AI Avatar creation has no form fields** — just a button that fires off a bare request with hardcoded `persona_preset: "energetic_beauty"`.
3. **Clone Avatar has no progress tracking** — just a toast "This may take a few minutes" and nothing else.
4. **No Avatar Library** — created avatars are invisible, no way to see/select/manage them.

## Research Summary — Best SaaS Avatar UIs
Based on HeyGen, Synthesia, D-ID patterns:

### Avatar Library (top of page)
- Grid of avatar cards showing: thumbnail, name, type (Clone/AI), status badge, created date
- Processing avatars show animated progress indicator
- Ready avatars show "Use" button
- Failed avatars show "Retry" button
- Active/selected avatar is highlighted

### AI Character Creation Form
Following HeyGen's "Create Avatar" flow:
1. **Name** — Text input for avatar name
2. **Description/Prompt** — What the avatar should look like (text prompt for image generation)
3. **Voice Style** — Dropdown: energetic, calm, professional, friendly, playful
4. **Background** — Dropdown: studio, living room, office, outdoor, transparent
5. **Camera Position** — Dropdown: close-up, waist-up, full body
6. **Style** — Dropdown: photorealistic, illustrated, anime
7. **AI Model** — Dropdown of available OpenRouter models for generation
8. **Persona Preset** — Selection cards (like current but with real content)

### Clone Flow
Following Synthesia's pattern:
1. Enter TikTok URL
2. Consent checkbox
3. Click "Clone" → show progress card with steps:
   - Step 1: Fetching TikTok videos... ✓
   - Step 2: Extracting face reference... ✓ 
   - Step 3: Cloning voice...
   - Step 4: Analyzing persona...
4. Poll `/api/avatar/status/{id}` every 3 seconds to show live progress

## Files to Modify

### Backend

#### `backend/orchestrator/tasks/__init__.py` — ALREADY FIXED
- Changed from `autodiscover_tasks` to explicit `include=[...]`
- Removed task_routes that routed to non-existent queues


#### `backend/orchestrator/models/avatar.py` — ADD FIELDS
Add these columns to Avatar model:
```python
name = Column(String, nullable=True)  # User-given name
description = Column(String, nullable=True)  # Generation prompt
voice_style = Column(String, nullable=True)  # energetic, calm, professional, friendly, playful
background = Column(String, nullable=True)  # studio, living_room, office, outdoor, transparent  
camera_position = Column(String, nullable=True)  # close_up, waist_up, full_body
style = Column(String, nullable=True)  # photorealistic, illustrated, anime
ai_model = Column(String, nullable=True)  # OpenRouter model ID
progress_step = Column(String, nullable=True)  # Current processing step description
progress_percent = Column(Float, default=0)  # 0-100 progress
```
#### `backend/orchestrator/routers/avatar.py` — EXPAND ENDPOINTS
1. Update `GenerateDigitalRequest` to accept all new fields
2. Add `GET /api/avatar/list` to list all user avatars
3. Update `AvatarStatusResponse` to include new fields + progress
4. Update `clone_from_tiktok` and `generate_digital` to pass new fields to tasks
5. Add `DELETE /api/avatar/{id}` endpoint

#### `backend/orchestrator/tasks/generate_avatar.py` — ADD PROGRESS TRACKING
- Update DB `progress_step` and `progress_percent` at each pipeline step
- For clone: "Fetching TikTok videos" (10%) → "Extracting face" (30%) → "Cloning voice" (60%) → "Analyzing persona" (85%) → "Ready" (100%)
- For digital: "Generating appearance" (25%) → "Creating voice" (50%) → "Building persona" (75%) → "Ready" (100%)

### Frontend

#### `frontend/companion-app/src/lib/types.ts` — UPDATE AVATAR INTERFACE
```typescript
export interface Avatar {
  id: string;
  type: AvatarType;
  status: AvatarStatus;
  name?: string;
  description?: string;
  voice_style?: string;
  background?: string;
  camera_position?: string;
  style?: string;
  ai_model?: string;
  face_ref_key?: string;
  voice_id?: string;
  persona_profile?: Record<string, unknown>;
  progress_step?: string;
  progress_percent?: number;
  created_at?: string;
  tiktok_source_url?: string;
}
```

#### `frontend/companion-app/src/lib/api.ts` — ADD LIST/DELETE
```typescript
export const avatarApi = {
  ...existing,
  list: () => api.get<{ avatars: Avatar[] }>("/avatar/list").then(r => r.data),
  delete: (id: string) => api.delete(`/avatar/${id}`),
};
```

#### `frontend/companion-app/src/pages/Setup.tsx` — COMPLETE REWRITE OF AVATAR TAB
New layout:
1. **Avatar Library** (top) — Grid of avatar cards with status, name, type badge
2. **Mode selector** — Clone / AI Character tabs (existing but improved)
3. **AI Character form** — Full form with name, description, voice, background, camera, style, model
4. **Clone flow** — Existing + progress tracking card that polls status

### Database Migration
Run SQL to add new columns:
```sql
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS name VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS description VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS voice_style VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS background VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS camera_position VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS style VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS ai_model VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS progress_step VARCHAR;
ALTER TABLE avatars ADD COLUMN IF NOT EXISTS progress_percent FLOAT DEFAULT 0;
```
