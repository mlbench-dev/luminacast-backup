"""PR-F — Stage-1 templates endpoint + create-cast-with-template (DB-backed).

Covers:
  * GET /api/casts/templates returns the 6 launch templates with the
    summary fields the Stage-1 picker needs.
  * POST /api/casts with a valid template_id persists it on the cast.
  * POST /api/casts with an unknown template_id falls back to null (Auto).

Skips automatically when the test Postgres is unreachable so the suite still
runs in lightweight environments (the schema/loader logic is covered without
a DB in tests/unit/test_cast_templates.py).
"""
import pytest

from models.user import User, UserRole
from models.avatar import Avatar, AvatarStatus


pytestmark = pytest.mark.asyncio


async def _seed_user_and_avatar(db):
    user = User(
        id="usr_test_sarah",
        email="sarah@test.com",
        password_hash="x",
        role=UserRole.CREATOR,
        is_active=True,
    )
    db.add(user)
    avatar = Avatar(
        id="av_tpl_test",
        user_id="usr_test_sarah",
        name="Test Avatar",
        status=AvatarStatus.READY,
    )
    db.add(avatar)
    await db.commit()
    return user, avatar


async def test_list_templates_endpoint(client, auth_headers, db_session):
    await _seed_user_and_avatar(db_session)
    resp = await client.get("/api/casts/templates", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 6
    ids = {t["id"] for t in body["templates"]}
    assert ids == {
        "talking_head_hook", "demo_heavy", "multi_angle_story",
        "social_proof_stack", "before_after_reveal", "mic_on_creator_vlog",
    }
    first = body["templates"][0]
    for key in ("name", "description", "block_count", "est_duration_range", "default_bias"):
        assert key in first


async def test_create_cast_with_valid_template(client, auth_headers, db_session):
    _, avatar = await _seed_user_and_avatar(db_session)
    resp = await client.post(
        "/api/casts",
        headers=auth_headers,
        json={
            "avatar_id": avatar.id,
            "quality": "hd",
            "description": "promote my serum",
            "template_id": "demo_heavy",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["template_id"] == "demo_heavy"


async def test_create_cast_with_unknown_template_falls_back_to_auto(client, auth_headers, db_session):
    _, avatar = await _seed_user_and_avatar(db_session)
    resp = await client.post(
        "/api/casts",
        headers=auth_headers,
        json={
            "avatar_id": avatar.id,
            "quality": "hd",
            "description": "promote my serum",
            "template_id": "not_a_real_template",
        },
    )
    assert resp.status_code == 201, resp.text
    # Unknown id is dropped — cast falls back to Auto (null).
    assert resp.json()["template_id"] is None
