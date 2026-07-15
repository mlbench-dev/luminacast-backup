# LUMINACAST OMNI — Test Skeleton Package
## Deploy directly into the luminacast-omni repo
### Companion to Dev Brief v4.1

---

## HOW TO USE

These test files are **skeletons** — they contain test structure and assertions that will FAIL until the corresponding feature is implemented. This is intentional.

**Workflow:**
1. Copy these files into the repo at the paths indicated
2. Run `make test` — all skeleton tests fail (expected)
3. Implement a feature (e.g., MashupEngine)
4. Run `make test-unit` — skeleton tests for MashupEngine now run against real code
5. Replace `assert False, "SKELETON"` with real assertions
6. Tests pass → feature is verified
7. Commit with green tests

**After every Perplexity coding session:** Run `bash infra/scripts/run-tests.sh` to verify nothing is broken.

---

## FILE: Makefile (project root)

```makefile
.PHONY: test test-unit test-integration test-frontend test-e2e test-all setup-test-db

# === Setup ===
setup-test-db:
	docker compose -f docker-compose.test.yml up -d
	sleep 3
	@echo "Test databases ready"

teardown-test-db:
	docker compose -f docker-compose.test.yml down

# === Backend ===
test-unit:
	cd backend/orchestrator && python -m pytest tests/unit/ -v --tb=short

test-integration: setup-test-db
	cd backend/orchestrator && python -m pytest tests/integration/ -v --tb=short

test-backend: test-unit test-integration

# === Frontend ===
test-frontend:
	cd frontend/companion-app && npx vitest run

# === E2E ===
test-e2e-onboard:
	cd frontend/companion-app && npx playwright test tests/e2e/onboarding.spec.ts

test-e2e-cast:
	cd frontend/companion-app && npx playwright test tests/e2e/cast-builder.spec.ts

test-e2e-live:
	cd frontend/companion-app && npx playwright test tests/e2e/live-control.spec.ts

test-e2e-analytics:
	cd frontend/companion-app && npx playwright test tests/e2e/analytics.spec.ts

test-e2e: test-e2e-onboard test-e2e-cast test-e2e-live test-e2e-analytics

# === All ===
test-all: test-backend test-frontend test-e2e

# === Quick (unit only — for rapid iteration) ===
test: test-unit test-frontend

# === CI (run on server after deploy) ===
ci: setup-test-db test-backend test-frontend teardown-test-db
```

---

## FILE: docker-compose.test.yml (project root)

```yaml
version: "3.8"

services:
  postgres-test:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: luminacast_test
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
    ports:
      - "5433:5432"
    tmpfs:
      - /var/lib/postgresql/data  # RAM disk for speed

  neo4j-test:
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/test
    ports:
      - "7688:7687"
    tmpfs:
      - /data

  redis-test:
    image: redis:7-alpine
    ports:
      - "6380:6379"
```

---

## FILE: infra/scripts/run-tests.sh

```bash
#!/bin/bash
# Run full test suite — use after each coding session
# Usage: bash infra/scripts/run-tests.sh

set -e  # Exit on first failure
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "========================================="
echo "  LUMINACAST OMNI — TEST RUNNER"
echo "========================================="
echo ""

# Start test databases
echo "📦 Starting test databases..."
docker compose -f docker-compose.test.yml up -d 2>/dev/null
sleep 3

PASS=0
FAIL=0
SKIP=0

run_test() {
    local name="$1"
    local cmd="$2"
    echo ""
    echo "--- $name ---"
    if eval "$cmd" 2>&1; then
        echo "✅ $name PASSED"
        PASS=$((PASS + 1))
    else
        echo "❌ $name FAILED"
        FAIL=$((FAIL + 1))
    fi
}

# Backend unit tests
run_test "Backend Unit Tests" \
    "cd backend/orchestrator && python -m pytest tests/unit/ -v --tb=short 2>&1"

# Backend integration tests
run_test "Backend Integration Tests" \
    "cd backend/orchestrator && python -m pytest tests/integration/ -v --tb=short 2>&1"

# Frontend unit tests
run_test "Frontend Unit Tests" \
    "cd frontend/companion-app && npx vitest run 2>&1"

# Cleanup
echo ""
echo "📦 Stopping test databases..."
docker compose -f docker-compose.test.yml down 2>/dev/null

# Summary
echo ""
echo "========================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "========================================="

if [ $FAIL -gt 0 ]; then
    echo "  ❌ Some tests failed — fix before committing"
    exit 1
else
    echo "  ✅ All tests passed — safe to commit"
    exit 0
fi
```

---

## FILE: backend/orchestrator/tests/conftest.py

```python
"""
Shared test fixtures for all backend tests.
Provides: test database, mock external services, test data factories.
"""
import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch

# Adjust imports to match your actual module structure
# from main import app
# from database import Base, get_db
# from config import settings

TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5433/luminacast_test"


# ==========================================
# DATABASE FIXTURES
# ==========================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for entire test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    """Create test database tables once per session."""
    engine = create_async_engine(TEST_DATABASE_URL)
    # Uncomment when Base is defined:
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.create_all)
    yield engine
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    """Fresh DB session per test, rolled back after each test."""
    async_session = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(db_session):
    """FastAPI test client with DB session override."""
    # Uncomment when app is built:
    # async def override_get_db():
    #     yield db_session
    # app.dependency_overrides[get_db] = override_get_db
    # transport = ASGITransport(app=app)
    # async with AsyncClient(transport=transport, base_url="http://test") as ac:
    #     yield ac
    # app.dependency_overrides.clear()
    yield None  # SKELETON — replace when app exists


# ==========================================
# MOCK EXTERNAL SERVICES
# All external APIs are mocked so tests never make real HTTP calls.
# Each mock returns predictable, deterministic values.
# ==========================================

@pytest.fixture
def mock_openrouter():
    """Mock OpenRouter API — LLM text + Nano Banana Pro images."""
    mock = AsyncMock()
    mock.generate_text.return_value = "Generated script text for testing purposes."
    mock.generate_image.return_value = "creators/test/scenes/test_scene.jpg"
    mock.analyze_persona.return_value = {
        "tone": "energetic, friendly",
        "energy_level": "high",
        "catchphrases": ["hey guys", "oh my god"],
        "vocabulary_level": "casual",
        "selling_style": "enthusiastic product demos",
        "emoji_patterns": ["✨", "💕"],
        "pacing": "fast, short sentences",
        "dos": ["smile", "show close-ups"],
        "donts": ["compare to competitors"],
        "greeting_style": "Hey everyone! Welcome back!",
        "closing_style": "Don't forget to follow!",
    }
    return mock


@pytest.fixture
def mock_fish_audio():
    """Mock Fish Audio API — voice cloning + TTS."""
    mock = AsyncMock()
    mock.clone_voice.return_value = "voice_id_test_123"
    mock.generate_tts.return_value = {
        "audio_key": "creators/test/audio/test.wav",
        "duration_seconds": 42.5,
    }
    return mock


@pytest.fixture
def mock_runpod():
    """Mock RunPod API — InfiniteTalk clip generation."""
    mock = AsyncMock()
    mock.submit_clip_job.return_value = "job_test_123"
    mock.poll_job_status.return_value = {
        "status": "COMPLETED",
        "output": {
            "video_r2_key": "creators/test/clips/test_clip.mp4",
            "duration_seconds": 42.5,
            "resolution": "720x1280",
            "status": "success",
        },
    }
    return mock


@pytest.fixture
def mock_r2():
    """Mock R2 storage — tracks uploads/downloads in memory dict."""
    mock = AsyncMock()
    mock._storage = {}  # in-memory store

    async def upload(local_path, key):
        mock._storage[key] = f"contents_of_{local_path}"
        return key

    async def download(key, local_path):
        return local_path

    def get_signed_url(key, expires_in=3600):
        return f"https://r2.test/{key}?expires={expires_in}"

    mock.upload_file = upload
    mock.download_file = download
    mock.get_signed_url = get_signed_url
    return mock


@pytest.fixture
def mock_stripe():
    """Mock Stripe API — payments, customers, usage."""
    mock = MagicMock()
    mock.create_customer.return_value = {"id": "cus_test_123"}
    mock.create_payment_intent.return_value = {
        "id": "pi_test_123",
        "status": "succeeded",
        "amount": 1499,
    }
    mock.report_usage.return_value = {"id": "mbur_test_123"}
    mock.create_checkout_session.return_value = {"url": "https://checkout.stripe.com/test"}
    return mock


@pytest.fixture
def mock_apify():
    """Mock Apify API — TikTok video scraping."""
    mock = AsyncMock()
    mock.fetch_tiktok_videos.return_value = [
        {
            "video_url": "https://tiktok.test/video1.mp4",
            "description": "Testing my new skincare routine ✨ #beauty",
            "stats": {"views": 50000, "likes": 3200},
        },
        {
            "video_url": "https://tiktok.test/video2.mp4",
            "description": "This serum changed my life!! 😍 #skincare",
            "stats": {"views": 120000, "likes": 8900},
        },
        {
            "video_url": "https://tiktok.test/video3.mp4",
            "description": "GRWM for date night 💕 #makeup #beauty",
            "stats": {"views": 75000, "likes": 5100},
        },
    ]
    # Failure scenario fixture
    mock.fetch_tiktok_videos_fail = AsyncMock(return_value=None)
    return mock


# ==========================================
# TEST DATA FACTORIES
# ==========================================

@pytest.fixture
def make_user(db_session):
    """Factory: create a test user in the database."""
    async def _make(email="sarah@test.com", role="creator"):
        # Uncomment when User model exists:
        # from models.user import User
        # user = User(
        #     id=f"usr_test_{email.split('@')[0]}",
        #     email=email,
        #     password_hash="$2b$12$test_hash_placeholder",
        #     role=role,
        #     stripe_customer_id=f"cus_test_{email.split('@')[0]}",
        # )
        # db_session.add(user)
        # await db_session.commit()
        # return user
        return {"id": f"usr_test_{email.split('@')[0]}", "email": email, "role": role}
    return _make


@pytest.fixture
def make_avatar(db_session):
    """Factory: create a test avatar."""
    async def _make(user_id, avatar_type="clone"):
        return {
            "id": f"avt_test_{user_id}",
            "user_id": user_id,
            "type": avatar_type,
            "status": "ready",
            "voice_id": "voice_test_123",
        }
    return _make


@pytest.fixture
def make_cast(db_session):
    """Factory: create a test cast."""
    async def _make(user_id, avatar_id, status="ready", num_blocks=8):
        return {
            "id": f"cst_test_{user_id}",
            "user_id": user_id,
            "avatar_id": avatar_id,
            "status": status,
            "template_name": "beauty_haul",
            "num_blocks": num_blocks,
        }
    return _make


@pytest.fixture
def make_stream_session(db_session):
    """Factory: create a test stream session."""
    async def _make(cast_id, user_id, duration_minutes=0):
        return {
            "id": f"ses_test_{cast_id}",
            "cast_id": cast_id,
            "user_id": user_id,
            "duration_minutes": duration_minutes,
            "total_purchases": 0,
            "total_gmv": 0.0,
        }
    return _make


@pytest.fixture
def auth_headers():
    """Generate JWT auth headers for a test creator."""
    # Uncomment when auth module exists:
    # from services.auth import create_access_token
    # token = create_access_token({"sub": "usr_test_sarah", "role": "creator"})
    # return {"Authorization": f"Bearer {token}"}
    return {"Authorization": "Bearer test_token_placeholder"}


@pytest.fixture
def admin_headers():
    """Generate JWT auth headers for an admin user."""
    return {"Authorization": "Bearer admin_token_placeholder"}
```

---

## FILE: backend/orchestrator/tests/unit/test_mashup_engine.py

```python
"""
Mashup Engine — variant selection, weighting, repeat prevention.
These tests define the expected behavior of the mashup engine.
Replace "SKELETON" assertions as the engine is implemented.
"""
import pytest


class TestSequentialMode:

    def test_returns_blocks_in_position_order(self):
        """Sequential mode: blocks play 1→2→3→...→N."""
        # engine = MashupEngine(mode="sequential")
        # blocks = [Block(position=i) for i in range(8)]
        # result = [engine.get_next() for _ in range(8)]
        # assert [b.position for b in result] == [0,1,2,3,4,5,6,7]
        assert False, "SKELETON — implement with real MashupEngine"

    def test_loops_after_all_blocks_played(self):
        """After all blocks played, restarts from intro."""
        assert False, "SKELETON — implement with real MashupEngine"

    def test_intro_is_always_first(self):
        """First block returned must be type=intro."""
        assert False, "SKELETON — implement with real MashupEngine"

    def test_closing_plays_after_all_products(self):
        """After all product blocks shown, next is closing."""
        assert False, "SKELETON — implement with real MashupEngine"


class TestShuffleMode:

    def test_no_consecutive_same_block(self):
        """Same block never plays twice in a row."""
        assert False, "SKELETON — implement with real MashupEngine"

    def test_intro_still_first_in_shuffle(self):
        """Even in shuffle, intro plays first."""
        assert False, "SKELETON — implement with real MashupEngine"

    def test_all_products_get_airtime(self):
        """Each product block plays at least once per cycle."""
        assert False, "SKELETON — implement with real MashupEngine"


class TestWeightedVariantSelection:

    def test_higher_score_selected_more_often(self):
        """Variant with score=0.9 selected more than score=0.1 over 1000 picks."""
        assert False, "SKELETON — implement with real variant selection"

    def test_zero_randomness_always_picks_first(self):
        """With randomness=0, always picks variant at index 0."""
        assert False, "SKELETON — implement with real variant selection"

    def test_full_randomness_distributes_evenly(self):
        """With randomness=1 and equal weights, distribution is roughly uniform."""
        assert False, "SKELETON — implement with real variant selection"


class TestSceneVariety:

    def test_no_same_scene_3x_consecutively(self):
        """Never plays same scene image 3 times in a row."""
        assert False, "SKELETON — implement with real scene selection"


class TestPerformanceScoring:

    def test_score_is_purchases_over_plays(self):
        """score = purchases_during / times_played."""
        # from engine.mashup import calculate_performance_score
        # assert abs(calculate_performance_score(8, 12) - 0.667) < 0.01
        assert False, "SKELETON — implement calculate_performance_score"

    def test_score_zero_when_no_plays(self):
        """Score = 0 when times_played = 0."""
        assert False, "SKELETON — implement calculate_performance_score"

    def test_score_capped_at_1(self):
        """Score never exceeds 1.0."""
        assert False, "SKELETON — implement calculate_performance_score"
```

---

## FILE: backend/orchestrator/tests/unit/test_billing.py

```python
"""
Billing calculations — Cast pricing, streaming cost, regen cost.
"""
import pytest


class TestCastCreationFee:

    def test_standard_fee_is_1499_cents(self):
        """Standard Cast = $14.99 (1499 cents)."""
        # fee = calculate_cast_creation_fee()
        # assert fee == 1499
        assert False, "SKELETON — implement billing module"


class TestStreamingCost:

    def test_cost_per_minute_is_3_cents(self):
        """$0.03/min = 3 cents/min."""
        # cost = calculate_streaming_cost(minutes=180)
        # assert cost == 540  # cents
        assert False, "SKELETON — implement billing module"

    def test_partial_minutes_rounded_up(self):
        """10.5 minutes billed as 11 minutes."""
        assert False, "SKELETON — implement billing module"

    def test_zero_minutes_costs_zero(self):
        """0 minutes = $0."""
        assert False, "SKELETON — implement billing module"


class TestClipRegenCost:

    def test_single_clip_is_99_cents(self):
        """1 clip regen = $0.99 (99 cents)."""
        assert False, "SKELETON — implement billing module"

    def test_multiple_clips(self):
        """3 clips = $2.97 (297 cents)."""
        assert False, "SKELETON — implement billing module"


class TestAvatarSetupCost:

    def test_setup_is_999_cents(self):
        """Avatar setup = $9.99 (999 cents)."""
        assert False, "SKELETON — implement billing module"
```

---

## FILE: backend/orchestrator/tests/unit/test_chat_classifier.py

```python
"""
Chat message classifier — keyword matching, product rules, LLM routing, safety.
"""
import pytest


class TestKeywordMatching:

    def test_shipping_question(self):
        """'how much is shipping' → Tier 1 keyword match."""
        assert False, "SKELETON — implement classifier"

    def test_price_question(self):
        """'what's the price' → Tier 1 keyword match."""
        assert False, "SKELETON — implement classifier"

    def test_case_insensitive(self):
        """'HOW MUCH SHIPPING?' matches same as lowercase."""
        assert False, "SKELETON — implement classifier"


class TestProductRules:

    def test_matches_product_specific_qa(self):
        """'is this for oily skin' matches product rule → Tier 2."""
        assert False, "SKELETON — implement classifier"

    def test_no_match_falls_to_llm(self):
        """Unknown question → Tier 3 LLM fallback."""
        assert False, "SKELETON — implement classifier"


class TestPurchaseDetection:

    def test_purchase_event_detected(self):
        """'🛒 @user bought Product!' flagged as purchase."""
        assert False, "SKELETON — implement classifier"

    def test_normal_message_not_purchase(self):
        """Regular question is NOT flagged as purchase."""
        assert False, "SKELETON — implement classifier"


class TestSafety:

    def test_response_under_280_chars(self):
        """All responses <= 280 characters."""
        assert False, "SKELETON — implement with LLM response validation"

    def test_no_medical_claims(self):
        """Response never contains 'cures', 'treats', 'heals'."""
        assert False, "SKELETON — implement safety filter"

    def test_abusive_message_ignored(self):
        """Hateful messages get no response."""
        assert False, "SKELETON — implement abuse detection"
```

---

## FILE: backend/orchestrator/tests/unit/test_afk_monitor.py

```python
"""
AFK monitoring — timers, escalation, overlay triggers.
"""
import pytest


class TestAFKTimer:

    def test_starts_on_product_block(self):
        """Timer starts when product block plays."""
        assert False, "SKELETON — implement AFKMonitor"

    def test_no_timer_on_intro_block(self):
        """Timer does NOT start for non-product blocks."""
        assert False, "SKELETON — implement AFKMonitor"

    def test_cancel_on_pin_confirm(self):
        """Timer cancels when creator confirms pin."""
        assert False, "SKELETON — implement AFKMonitor"

    def test_afk_fires_after_timeout(self):
        """AFK event triggers when timer reaches 0."""
        assert False, "SKELETON — implement AFKMonitor"

    def test_custom_timeout_per_block(self):
        """Different blocks can have different timeouts."""
        assert False, "SKELETON — implement AFKMonitor"


class TestAFKEscalation:

    def test_1_afk_is_overlay_only(self):
        """First AFK = overlay + chat CTA."""
        assert False, "SKELETON — implement escalation"

    def test_5_consecutive_suggests_pause(self):
        """5 consecutive AFKs → suggest pause."""
        assert False, "SKELETON — implement escalation"

    def test_10_consecutive_auto_idle(self):
        """10+ AFKs → switch to idle loop."""
        assert False, "SKELETON — implement escalation"
```

---

## FILE: backend/orchestrator/tests/integration/test_auth_api.py

```python
"""
Auth API — registration, login, JWT, role-gating.
"""
import pytest


class TestRegistration:

    @pytest.mark.asyncio
    async def test_register_success(self, client, mock_stripe):
        """POST /api/auth/register → 201 + JWT token."""
        assert False, "SKELETON — implement when auth routes built"

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client):
        """Duplicate email → 409 Conflict."""
        assert False, "SKELETON — implement when auth routes built"


class TestLogin:

    @pytest.mark.asyncio
    async def test_login_success(self, client, make_user):
        """POST /api/auth/login → 200 + JWT."""
        assert False, "SKELETON — implement when auth routes built"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client, make_user):
        """Wrong password → 401."""
        assert False, "SKELETON — implement when auth routes built"


class TestRoleGating:

    @pytest.mark.asyncio
    async def test_creator_blocked_from_admin(self, client, auth_headers):
        """Creator JWT → /api/admin/* → 403."""
        assert False, "SKELETON — implement when role middleware built"

    @pytest.mark.asyncio
    async def test_admin_can_access_admin(self, client, admin_headers):
        """Admin JWT → /api/admin/* → 200."""
        assert False, "SKELETON — implement when role middleware built"


class TestMultiTenant:

    @pytest.mark.asyncio
    async def test_creator_sees_only_own_casts(self, client):
        """Creator A cannot see Creator B's casts."""
        assert False, "SKELETON — implement when isolation tested"
```

---

## FILE: backend/orchestrator/tests/integration/test_cast_api.py

```python
"""
Cast CRUD and generation pipeline.
"""
import pytest


class TestCastCRUD:

    @pytest.mark.asyncio
    async def test_create_cast(self, client, auth_headers):
        """POST /api/casts → 201, status=draft."""
        assert False, "SKELETON — implement when cast routes built"

    @pytest.mark.asyncio
    async def test_add_products_to_cast(self, client, auth_headers):
        """PUT /api/casts/{id}/products → updates product list."""
        assert False, "SKELETON — implement when products wired"

    @pytest.mark.asyncio
    async def test_delete_cast(self, client, auth_headers):
        """DELETE /api/casts/{id} → 204, removes from DB + R2."""
        assert False, "SKELETON — implement when delete route built"


class TestCastGeneration:

    @pytest.mark.asyncio
    async def test_generate_outline(self, client, auth_headers, mock_openrouter):
        """POST /api/casts/{id}/generate-outline → returns scenes."""
        assert False, "SKELETON — implement when outline generation wired"

    @pytest.mark.asyncio
    async def test_pay_triggers_generation(self, client, auth_headers, mock_stripe):
        """POST /api/casts/{id}/pay → charges Stripe, status=generating."""
        assert False, "SKELETON — implement when Stripe wired"

    @pytest.mark.asyncio
    async def test_generation_progress(self, client, auth_headers):
        """GET /api/casts/{id}/generation-status → progress float."""
        assert False, "SKELETON — implement when generation pipeline wired"

    @pytest.mark.asyncio
    async def test_generation_failure_marks_cast(self, client, auth_headers, mock_runpod):
        """When >30% variants fail → cast status=generation_failed."""
        assert False, "SKELETON — implement when error policy coded"
```

---

## FILE: backend/orchestrator/tests/integration/test_stream_api.py

```python
"""
Stream control endpoints.
"""
import pytest


class TestStreamLifecycle:

    @pytest.mark.asyncio
    async def test_start_stream(self, client, auth_headers):
        """POST /api/stream/start → creates session, starts FFmpeg."""
        assert False, "SKELETON — implement when stream start built"

    @pytest.mark.asyncio
    async def test_no_double_start(self, client, auth_headers):
        """Second start for same cast → 409."""
        assert False, "SKELETON — implement when uniqueness enforced"

    @pytest.mark.asyncio
    async def test_stop_stream(self, client, auth_headers, mock_stripe):
        """POST /api/stream/stop → finalizes billing, kills FFmpeg."""
        assert False, "SKELETON — implement when stop route built"

    @pytest.mark.asyncio
    async def test_skip_advances_block(self, client, auth_headers):
        """POST /api/stream/skip → next block plays."""
        assert False, "SKELETON — implement when skip wired"

    @pytest.mark.asyncio
    async def test_pause_plays_idle(self, client, auth_headers):
        """POST /api/stream/pause → idle loop plays."""
        assert False, "SKELETON — implement when pause wired"


class TestPinConfirm:

    @pytest.mark.asyncio
    async def test_pin_confirm_cancels_timer(self, client, auth_headers):
        """POST /api/stream/pin-confirm → AFK timer stops."""
        assert False, "SKELETON — implement when AFK wired"
```

---

## FILE: frontend/companion-app/tests/e2e/onboarding.spec.ts

```typescript
import { test, expect } from '@playwright/test';

test.describe('Journey 1: Onboarding', () => {
  test('can register with email', async ({ page }) => {
    await page.goto('/register');
    await page.fill('[data-testid="email"]', 'e2e@test.com');
    await page.fill('[data-testid="password"]', 'TestPass123!');
    await page.click('[data-testid="register-btn"]');
    await expect(page).toHaveURL(/dashboard/);
  });

  test('can navigate to avatar setup', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can enter TikTok URL for cloning', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('sees avatar processing progress', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can approve completed avatar', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });
});
```

---

## FILE: frontend/companion-app/tests/e2e/cast-builder.spec.ts

```typescript
import { test, expect } from '@playwright/test';

test.describe('Journey 2: Cast Builder', () => {
  test('can create new Cast', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can add products', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can generate and review script outline', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can select template', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can pay with Stripe test card', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('sees generation progress', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can preview and approve clips', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });
});
```

---

## FILE: frontend/companion-app/tests/e2e/live-control.spec.ts

```typescript
import { test, expect } from '@playwright/test';

test.describe('Journey 3: Live Control', () => {
  test('can start stream from ready Cast', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('NOW PLAYING bar shows current block', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can skip to next block', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('chat messages appear in real-time', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can approve AI chat draft', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('AFK warning appears for unconfirmed pin', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('pin confirm cancels AFK timer', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('can stop stream and see billing', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });
});
```

---

## FILE: frontend/companion-app/tests/e2e/analytics.spec.ts

```typescript
import { test, expect } from '@playwright/test';

test.describe('Journey 4: Analytics', () => {
  test('shows stream session list', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('shows per-product breakdown', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('shows variant performance scores', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });

  test('renders engagement timeline chart', async ({ page }) => {
    test.skip(true, 'SKELETON');
  });
});
```

---

## FILE: frontend/companion-app/tests/unit/streamStore.test.ts

```typescript
import { describe, it, expect, beforeEach } from 'vitest';
// import { useStreamStore } from '../../src/stores/streamStore';

describe('streamStore', () => {
  it('starts with offline status', () => {
    // expect(useStreamStore.getState().status).toBe('offline');
    expect(false).toBe(true); // SKELETON
  });

  it('updates on BLOCK_TRANSITION event', () => {
    expect(false).toBe(true); // SKELETON
  });

  it('starts AFK timer on product block', () => {
    expect(false).toBe(true); // SKELETON
  });

  it('cancels AFK timer on PIN_CONFIRM', () => {
    expect(false).toBe(true); // SKELETON
  });

  it('tracks operator presence', () => {
    expect(false).toBe(true); // SKELETON
  });

  it('updates stats on purchase event', () => {
    expect(false).toBe(true); // SKELETON
  });
});
```

---

## FILE: frontend/companion-app/tests/unit/billing.test.ts

```typescript
import { describe, it, expect } from 'vitest';
// import { formatStreamingCost, formatCastPrice, ceilMinutes } from '../../src/lib/billing';

describe('billing utilities', () => {
  it('formats streaming cost as $X.XX', () => {
    // expect(formatStreamingCost(180)).toBe('$5.40');
    expect(false).toBe(true); // SKELETON
  });

  it('formats cast price as $14.99', () => {
    // expect(formatCastPrice()).toBe('$14.99');
    expect(false).toBe(true); // SKELETON
  });

  it('rounds up partial minutes', () => {
    // expect(ceilMinutes(10.5)).toBe(11);
    expect(false).toBe(true); // SKELETON
  });

  it('zero minutes costs zero', () => {
    // expect(formatStreamingCost(0)).toBe('$0.00');
    expect(false).toBe(true); // SKELETON
  });
});
```

---

## TOTAL TEST COUNT

| Category | File | Tests | Status |
|----------|------|-------|--------|
| Unit: Mashup | test_mashup_engine.py | 13 | SKELETON |
| Unit: Billing | test_billing.py | 7 | SKELETON |
| Unit: Chat | test_chat_classifier.py | 9 | SKELETON |
| Unit: AFK | test_afk_monitor.py | 8 | SKELETON |
| Integration: Auth | test_auth_api.py | 6 | SKELETON |
| Integration: Cast | test_cast_api.py | 6 | SKELETON |
| Integration: Stream | test_stream_api.py | 6 | SKELETON |
| Frontend: Store | streamStore.test.ts | 6 | SKELETON |
| Frontend: Billing | billing.test.ts | 4 | SKELETON |
| E2E: Onboarding | onboarding.spec.ts | 5 | SKELETON |
| E2E: Cast Builder | cast-builder.spec.ts | 7 | SKELETON |
| E2E: Live Control | live-control.spec.ts | 8 | SKELETON |
| E2E: Analytics | analytics.spec.ts | 4 | SKELETON |
| **TOTAL** | **13 files** | **89 tests** | **All SKELETON** |

When development is complete, all 89 tests should pass. Any remaining SKELETON assertions indicate unimplemented features.
