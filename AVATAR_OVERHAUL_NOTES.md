# Avatar Overhaul Notes — 2026-04-11

## Pre-existing observations (before any changes)

### AIAvatarSetup.tsx
- Creates avatar on mount with no resume support — `?resume=` param not handled
- Hover preview popup (lines 527-538) covers other faces — confirmed for Phase 3 fix
- Preview phase (line 865) hardcodes "Voice: Custom ElevenLabs → Fish Audio clone" — engine name leak
- Preview phase has separate "Edit face" / "Edit voice" / "Regenerate" buttons (lines 919-927) — Phase 5 wants single button
- Preview video maxWidth is 220px — small (Phase 5.2)
- testScript textarea in preview phase repeats the script question (Phase 5.1)
- No `?resume=` param handling — Phase 1.3

### Setup.tsx
- Line 246: all clickable-for-resume avatars navigate to `/my-avatar/clone?resume=` regardless of avatar type — confirmed Phase 1 bug
- Avatar type is differentiated visually (Camera vs Wand2 icons) but not in routing logic
- Frontend enum: AvatarType.CLONE="clone", AvatarType.DIGITAL="digital" (NOTE: "digital" not "ai_avatar")

### generate_avatar.py
- Step 4 progress text: "Generating talking video via InfiniteTalk..." — engine name leak (Phase 7)
- Uses `_update_progress` for all steps
- VOICE_STYLE_MAP maps to Fish Audio public voice IDs
- TEST_SCRIPT hardcoded at top of file

### avatar_looks.py
- Task `generate_all_body_motion` is registered as `tasks.avatar_looks.generate_all_body_motion`
- It IS in the celery_app includes — need to investigate why it fails

### Celery config
- `tasks.avatar_looks` IS in the include list — task should be discoverable
- Possible issue: task name mismatch or import error at module load time

## Phase status tracker
- Phase 0: ✅ Setup complete
- Phase 1: ⬜ Pending
- Phase 2: ⬜ Pending
- Phase 3: ⬜ Pending
- Phase 4: ⬜ Pending
- Phase 5: ⬜ Pending
- Phase 6: ⬜ Pending
- Phase 7: ⬜ Pending
- Phase 8: ⬜ Pending
- Phase 9: ⬜ Pending
- Phase 10: ⬜ Pending
- Phase 11: ⬜ Pending
- Phase 12: ⬜ Pending
- Phase 13: ⬜ Pending
