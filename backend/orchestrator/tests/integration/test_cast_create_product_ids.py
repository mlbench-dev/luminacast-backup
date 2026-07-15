"""Bug 3 regression — POST /api/casts must NOT silently drop unknown/unowned
product_ids.

Before the fix, ``create_cast`` skipped any product_id that didn't exist or
wasn't owned by the caller with no log, error, or Sentry breadcrumb — the user
got a cast attached to the wrong/no product and it was undebuggable. The
endpoint now returns HTTP 422 with the dropped ids and captures one Sentry
message per request.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from models.user import User, UserRole
from models.product import Product
from models.avatar import Avatar, AvatarType, AvatarStatus


# auth_headers issues a token for sub="usr_test_sarah", so the persisted user
# id must match for get_current_user to resolve the caller.
SARAH_ID = "usr_test_sarah"


async def _seed(db_session):
    from passlib.context import CryptContext
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    sarah = User(
        id=SARAH_ID, email="sarah@test.com",
        password_hash=pwd.hash("TestPass123!"), role=UserRole.CREATOR,
        is_active=True,
    )
    other = User(
        id=f"usr_other_{uuid.uuid4().hex[:8]}", email="other@test.com",
        password_hash=pwd.hash("TestPass123!"), role=UserRole.CREATOR,
        is_active=True,
    )
    db_session.add_all([sarah, other])

    avatar = Avatar(
        id=f"avt_{uuid.uuid4().hex[:8]}", user_id=SARAH_ID,
        type=AvatarType("clone"), status=AvatarStatus.READY,
        voice_id="voice_test_123",
    )
    db_session.add(avatar)

    my_product = Product(
        id=f"prod_{uuid.uuid4().hex[:8]}", user_id=SARAH_ID,
        name="My Widget", price=19.99,
    )
    other_product = Product(
        id=f"prod_{uuid.uuid4().hex[:8]}", user_id=other.id,
        name="Not Mine", price=29.99,
    )
    db_session.add_all([my_product, other_product])
    await db_session.commit()
    return avatar, my_product, other_product


@pytest.mark.asyncio
async def test_create_cast_rejects_unowned_product_ids(client, auth_headers, db_session):
    avatar, my_product, other_product = await _seed(db_session)
    bogus_id = "prod_does_not_exist"

    with patch("routers.casts.sentry_sdk.capture_message") as cap:
        resp = await client.post(
            "/api/casts",
            headers=auth_headers,
            json={
                "avatar_id": avatar.id,
                "product_ids": [my_product.id, other_product.id, bogus_id],
            },
        )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    # FastAPI wraps the raised detail under "detail".
    detail = body.get("detail", body)
    dropped = detail.get("dropped_product_ids")
    assert set(dropped) == {other_product.id, bogus_id}
    assert my_product.id not in dropped

    # Exactly one Sentry capture per request that drops ids.
    assert cap.call_count == 1


@pytest.mark.asyncio
async def test_create_cast_accepts_owned_product_ids(client, auth_headers, db_session):
    avatar, my_product, _other = await _seed(db_session)

    with patch("routers.casts.sentry_sdk.capture_message") as cap:
        resp = await client.post(
            "/api/casts",
            headers=auth_headers,
            json={
                "avatar_id": avatar.id,
                "product_ids": [my_product.id],
            },
        )

    assert resp.status_code in (200, 201), resp.text
    cap.assert_not_called()
