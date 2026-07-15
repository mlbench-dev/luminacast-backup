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

from main import app
from database import Base, get_db
from config import settings

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
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
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ==========================================
# MOCK EXTERNAL SERVICES
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
    mock._storage = {}

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
    mock.fetch_tiktok_videos_fail = AsyncMock(return_value=None)
    return mock


# ==========================================
# TEST DATA FACTORIES
# ==========================================

@pytest.fixture
def make_user(db_session):
    """Factory: create a test user in the database."""
    async def _make(email="sarah@test.com", role="creator"):
        from models.user import User, UserRole
        from passlib.context import CryptContext
        import uuid
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        user = User(
            id=f"usr_test_{uuid.uuid4().hex[:8]}",
            email=email,
            password_hash=pwd_context.hash("TestPass123!"),
            role=UserRole(role),
        )
        db_session.add(user)
        await db_session.commit()
        return user
    return _make


@pytest.fixture
def make_avatar(db_session):
    """Factory: create a test avatar."""
    async def _make(user_id, avatar_type="clone"):
        from models.avatar import Avatar, AvatarType, AvatarStatus
        import uuid
        avatar = Avatar(
            id=f"avt_test_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            type=AvatarType(avatar_type),
            status=AvatarStatus.READY,
            voice_id="voice_test_123",
        )
        db_session.add(avatar)
        await db_session.commit()
        return avatar
    return _make


@pytest.fixture
def make_cast(db_session):
    """Factory: create a test cast."""
    async def _make(user_id, avatar_id, status="ready", num_blocks=8):
        from models.cast import Cast, CastStatus
        import uuid
        cast = Cast(
            id=f"cst_test_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            avatar_id=avatar_id,
            name="Test Cast",
            status=CastStatus(status),
            template_name="beauty_haul",
        )
        db_session.add(cast)
        await db_session.commit()
        return cast
    return _make


@pytest.fixture
def make_stream_session(db_session):
    """Factory: create a test stream session."""
    async def _make(cast_id, user_id, duration_minutes=0):
        from models.stream_session import StreamSession
        import uuid
        session = StreamSession(
            id=f"ses_test_{uuid.uuid4().hex[:8]}",
            cast_id=cast_id,
            user_id=user_id,
            duration_minutes=duration_minutes,
        )
        db_session.add(session)
        await db_session.commit()
        return session
    return _make


@pytest.fixture
def auth_headers():
    """Generate JWT auth headers for a test creator."""
    from routers.auth import create_access_token
    token = create_access_token({"sub": "usr_test_sarah", "role": "creator", "email": "sarah@test.com"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    """Generate JWT auth headers for an admin user."""
    from routers.auth import create_access_token
    token = create_access_token({"sub": "usr_test_admin", "role": "admin", "email": "admin@luminacast.com"})
    return {"Authorization": f"Bearer {token}"}
