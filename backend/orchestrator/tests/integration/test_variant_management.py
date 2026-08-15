"""
Phase 2.1 — Per-block audio regeneration + variant management API tests.

Tests:
1. Regenerate audio → new variant exists, is active, old is inactive
2. List variants → returns both, active flag correct
3. Select variant → flips active state correctly
4. Auth boundary → user cannot regenerate another user's cast
"""
import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock


# ── Helpers ──

async def _seed_user_avatar_cast_block(db_session):
    """Create a user, avatar, cast, block, and one variant for testing."""
    from models.user import User, UserRole
    from models.avatar import Avatar, AvatarType, AvatarStatus
    from models.cast import Cast, CastStatus
    from models.block import Block, BlockType
    from models.variant import Variant, VariantStatus
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    user_id = f"usr_test_{uuid.uuid4().hex[:8]}"
    user = User(
        id=user_id,
        email=f"{user_id}@test.com",
        password_hash=pwd_context.hash("TestPass123!"),
        role=UserRole.CREATOR,
    )
    db_session.add(user)

    avatar = Avatar(
        id=f"avt_test_{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        type=AvatarType.CLONE,
        status=AvatarStatus.APPROVED,
        voice_id="voices/refs/test123/reference.wav",
    )
    db_session.add(avatar)

    cast_id = f"cst_test_{uuid.uuid4().hex[:8]}"
    cast = Cast(
        id=cast_id,
        user_id=user_id,
        avatar_id=avatar.id,
        name="Test Cast",
        status=CastStatus.TTS_READY,
    )
    db_session.add(cast)

    block_id = f"blk_test_{uuid.uuid4().hex[:8]}"
    block = Block(
        id=block_id,
        cast_id=cast_id,
        type=BlockType.PRODUCT,
        position=0,
    )
    db_session.add(block)

    variant = Variant(
        id=f"var_test_{uuid.uuid4().hex[:8]}",
        block_id=block_id,
        script_text="Original script text",
        status=VariantStatus.READY,
        is_active=True,
        audio_key="tts/test/original.mp3",
        tts_duration_seconds=5.0,
        duration_seconds=5.0,
    )
    db_session.add(variant)

    await db_session.commit()
    return user, avatar, cast, block, variant


def _auth_headers_for(user_id: str, email: str) -> dict:
    from routers.auth import create_access_token
    token = create_access_token({"sub": user_id, "role": "creator", "email": email})
    return {"Authorization": f"Bearer {token}"}


def _mock_fish_audio():
    """Return a mock FishAudioService that returns predictable TTS results."""
    mock = AsyncMock()
    mock.generate_tts.return_value = {
        "audio_key": "tts/test/regenerated.mp3",
        "duration_seconds": 6.5,
        "tmp_path": "/tmp/test.mp3",
    }
    return mock


def _mock_r2():
    """Return a mock R2 storage service."""
    mock = MagicMock()
    mock.get_public_url.side_effect = lambda key: f"https://media.luminacast.com/{key}" if key else None
    mock.get_signed_url.side_effect = lambda key, **kw: f"https://r2.test/{key}" if key else None
    return mock


# ── Tests ──

class TestRegenerateBlockAudio:

    @pytest.mark.asyncio
    async def test_regenerate_creates_new_active_variant(self, client, db_session):
        """POST regenerate-audio → new variant is active, old is inactive."""
        user, avatar, cast, block, old_variant = await _seed_user_avatar_cast_block(db_session)
        headers = _auth_headers_for(user.id, user.email)

        with patch("routers.casts.variants.get_fish_audio_service", return_value=_mock_fish_audio()) as _, \
             patch("services.r2_storage.get_r2_storage_service", return_value=_mock_r2()):

            # Patch at the import location used in the endpoint
            import routers.casts.variants as casts_module
            original_import = None

            resp = await client.post(
                f"/api/casts/{cast.id}/blocks/{block.id}/regenerate-audio",
                json={"script_text": "New regenerated script"},
                headers=headers,
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "variant_id" in data
        assert data["duration_seconds"] == 6.5
        assert "audio_url" in data
        assert data["variant_id"] != old_variant.id

        # Verify old variant is now inactive
        from models.variant import Variant
        await db_session.refresh(old_variant)
        assert old_variant.is_active is False

        # Verify new variant is active
        new_var = await db_session.get(Variant, data["variant_id"])
        assert new_var is not None
        assert new_var.is_active is True
        assert new_var.script_text == "New regenerated script"

    @pytest.mark.asyncio
    async def test_regenerate_auth_boundary(self, client, db_session):
        """User cannot regenerate another user's cast."""
        user, avatar, cast, block, variant = await _seed_user_avatar_cast_block(db_session)

        # Create a different user
        other_headers = _auth_headers_for("usr_test_other", "other@test.com")

        resp = await client.post(
            f"/api/casts/{cast.id}/blocks/{block.id}/regenerate-audio",
            json={"script_text": "Hacked script"},
            headers=other_headers,
        )

        assert resp.status_code == 404


class TestListBlockVariants:

    @pytest.mark.asyncio
    async def test_list_returns_all_variants(self, client, db_session):
        """GET variants → returns both active and inactive."""
        user, avatar, cast, block, variant = await _seed_user_avatar_cast_block(db_session)
        headers = _auth_headers_for(user.id, user.email)

        # Add a second inactive variant
        from models.variant import Variant, VariantStatus
        second = Variant(
            id=f"var_test_{uuid.uuid4().hex[:8]}",
            block_id=block.id,
            script_text="Older script",
            status=VariantStatus.READY,
            is_active=False,
            audio_key="tts/test/older.mp3",
            tts_duration_seconds=4.0,
        )
        db_session.add(second)
        await db_session.commit()

        with patch("services.r2_storage.get_r2_storage_service", return_value=_mock_r2()):
            resp = await client.get(
                f"/api/casts/{cast.id}/blocks/{block.id}/variants",
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        active_variants = [v for v in data if v["is_active"]]
        inactive_variants = [v for v in data if not v["is_active"]]
        assert len(active_variants) == 1
        assert len(inactive_variants) == 1
        assert active_variants[0]["variant_id"] == variant.id


class TestSelectBlockVariant:

    @pytest.mark.asyncio
    async def test_select_flips_active_state(self, client, db_session):
        """PATCH select-variant → chosen variant active, others inactive."""
        user, avatar, cast, block, variant_a = await _seed_user_avatar_cast_block(db_session)
        headers = _auth_headers_for(user.id, user.email)

        # Add variant B (inactive)
        from models.variant import Variant, VariantStatus
        variant_b = Variant(
            id=f"var_test_{uuid.uuid4().hex[:8]}",
            block_id=block.id,
            script_text="Variant B script",
            status=VariantStatus.READY,
            is_active=False,
            audio_key="tts/test/variant_b.mp3",
            tts_duration_seconds=7.0,
        )
        db_session.add(variant_b)
        await db_session.commit()

        with patch("services.r2_storage.get_r2_storage_service", return_value=_mock_r2()):
            resp = await client.patch(
                f"/api/casts/{cast.id}/blocks/{block.id}/select-variant",
                json={"variant_id": variant_b.id},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["variant_id"] == variant_b.id
        assert data["duration_seconds"] == 7.0

        # Verify: A is now inactive, B is active
        await db_session.refresh(variant_a)
        await db_session.refresh(variant_b)
        assert variant_a.is_active is False
        assert variant_b.is_active is True

    @pytest.mark.asyncio
    async def test_select_wrong_variant_404(self, client, db_session):
        """Cannot select a variant that doesn't belong to this block."""
        user, avatar, cast, block, variant = await _seed_user_avatar_cast_block(db_session)
        headers = _auth_headers_for(user.id, user.email)

        resp = await client.patch(
            f"/api/casts/{cast.id}/blocks/{block.id}/select-variant",
            json={"variant_id": "var_nonexistent"},
            headers=headers,
        )

        assert resp.status_code == 404
