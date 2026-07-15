"""Step 1 — layout-template preset seeder (DB-backed).

  - Idempotency: running the seeder twice yields exactly 10 preset rows
    (is_preset=True, user_id IS NULL), no duplicates.
  - API: GET /api/layout-templates returns all 10 presets to any
    authenticated user (tested against the router's current behavior).
"""
import pytest
from sqlalchemy import func, select

from models.layout_template import LayoutTemplate
from scripts.seed_layout_templates import PRESETS, _preset_id, upsert_presets


@pytest.mark.asyncio
async def test_seeder_is_idempotent(db_session):
    """Run the seeder twice → exactly 10 preset rows, no duplicates."""
    first = await upsert_presets(db_session)
    await db_session.commit()
    assert first == {"inserted": 10, "updated": 0}

    second = await upsert_presets(db_session)
    await db_session.commit()
    assert second == {"inserted": 0, "updated": 10}

    total = await db_session.scalar(
        select(func.count())
        .select_from(LayoutTemplate)
        .where(LayoutTemplate.is_preset == True)  # noqa: E712
        .where(LayoutTemplate.user_id.is_(None))
    )
    assert total == 10

    rows = (
        await db_session.execute(
            select(LayoutTemplate.id).where(LayoutTemplate.is_preset == True)  # noqa: E712
        )
    ).scalars().all()
    assert len(rows) == len(set(rows)) == 10


@pytest.mark.asyncio
async def test_list_endpoint_returns_all_presets(client, db_session, make_user):
    """GET /api/layout-templates returns all 10 presets to any authenticated user."""
    from routers.auth import create_access_token

    await upsert_presets(db_session)
    await db_session.commit()

    user = await make_user(email="presets@test.com", role="creator")
    token = create_access_token({"sub": user.id, "role": "creator", "email": user.email})

    resp = await client.get(
        "/api/layout-templates", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()

    preset_ids = {t["id"] for t in body["templates"] if t["is_preset"]}
    expected = {_preset_id(slug) for slug, _, _ in PRESETS}
    assert expected.issubset(preset_ids)

    for t in body["templates"]:
        if t["is_preset"]:
            assert t["user_id"] is None
