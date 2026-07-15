# Luminacast Omni — QA Bug Report
## Date: 2026-03-26

### CRITICAL BUGS (Blocking end-to-end flow)

1. **[BUG-001] Cast Builder: Create Cast 500 — FK violation on avatar_id='default'**
   - Frontend hardcodes `avatar_id: "default"` but no default avatar exists in DB
   - Fix: Seed a default avatar OR make avatar_id nullable OR auto-create

2. **[BUG-002] Admin Dashboard: 500 — timezone-aware vs naive datetime**
   - `billing_events.created_at` is TIMESTAMP WITHOUT TIME ZONE
   - Query passes `datetime.now(timezone.utc)` which is timezone-aware
   - Fix: Use naive datetime in the query

3. **[BUG-003] Admin Dashboard: Missing fields — server_health lacks cpu/memory/disk**
   - Dashboard returns `server_health: {status, uptime}` but frontend expects `{cpu_percent, memory_percent, disk_percent}`
   - Fix: Merge /health response into /dashboard response

4. **[BUG-004] Admin Creators: Missing fields — no total_casts, total_streams, total_revenue_cents**
   - API returns only `{id, email, tiktok_handle, created_at, is_active}`
   - Frontend expects `{total_casts, total_streams, total_revenue_cents}` → causes $NaN
   - Fix: Add aggregate queries to the creators endpoint

### HIGH BUGS

5. **[BUG-005] Setup/Voice: Play Preview button does nothing**
   - No onClick handler attached — purely cosmetic UI
   - Fix: Add toast or actual TTS preview via FishAudio API

6. **[BUG-006] Setup/Billing: Stripe Portal button does nothing**
   - No onClick handler — dead button
   - Fix: Add Stripe customer portal redirect or toast

7. **[BUG-007] Admin Streams: Missing creator_email, status, uptime_seconds, viewers**
   - API returns different fields than frontend expects
   - Fix: Add these fields to the admin streams response

### MEDIUM BUGS

8. **[BUG-008] Auth: Protected routes don't redirect URL to /login**
   - ProtectedRoute renders login but URL stays at /dashboard
   - Fix: Use Navigate component to redirect

9. **[BUG-009] Admin: Total Creators shows 0 — only counts CREATOR role, admin is ADMIN role**
   - Fix: Count all non-admin users OR count all users

10. **[BUG-010] Cast Builder: No validation toast for < 3 products**
    - Actually there IS code for this but button is just disabled
    - Fix: Add explicit toast message

### MINOR BUGS

11. **[BUG-011] AI Character preset images are grey placeholders**
12. **[BUG-012] Cancel in Cast Builder has no confirmation**
13. **[BUG-013] Remove product has no confirmation**
14. **[BUG-014] Add Product button not visually disabled at 8/8**
15. **[BUG-015] Team invite doesn't persist (frontend-only toast)**
16. **[BUG-016] Stream key save is session-only (frontend-only toast)**
