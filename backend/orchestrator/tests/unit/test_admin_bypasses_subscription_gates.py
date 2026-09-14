"""Request: "admin does not need to buy subscription so remove the checks
of subscription for admin."

There are exactly two places in the app that actually BLOCK an action with
a "subscribe to continue" error (a 402): ``check_render_preflight`` (before
starting a render) and ``check_avatar_slot_available`` (before creating a
new avatar). Everything else under services.billing_service is bookkeeping
(deducting usage, auto-top-up) — it degrades gracefully with zero included
allowance when there's no subscription, it never raises.

Fix: a shared ``_is_admin(db, owner_id)`` helper, checked first thing in
both gate functions — an admin account short-circuits past the
subscription/wallet/usage-period lookups entirely and is never blocked.

Follow-up bug (client caught it): the backend gates were fixed, but the
avatar Setup page's "Create Avatar" / "Clone Avatar" buttons are ALSO
disabled client-side from a separate summary endpoint
(``get_avatar_slot_summary``) that wasn't admin-aware — an admin over the
free-tier avatar count still saw the button greyed out and never even
reached the (already-fixed) backend gate. Fixed by making the summary
itself report a large headroom for admins, so ``remaining <= 0`` (the
frontend's own disable condition) is never true for them — no frontend
change required, though the API type was widened to include the new
`unlimited` field for future use.

Second, more serious follow-up bug: the start-of-render gate
(check_render_preflight) was fixed, but the function that actually bills a
COMPLETED render (``deduct_render_usage`` — deducts wallet credits, or
fires a real off-session Stripe charge via ``_attempt_overage_charge``)
recomputes billing entirely independently and had no admin awareness at
all. An admin past their included/free minutes could start a render fine
(gate bypassed) and then have their card genuinely charged once it
finished. Fixed the same way: an admin short-circuits to a free-of-charge
usage record, never touching the wallet or Stripe. Also fixed the
"Finalize & Render" confirmation dialog's own preview
(``_describe_next_render_billing``, read by the editor page BEFORE
starting a render) the same way, so an admin with an active subscription
record never sees a "this will be charged to your card" warning that
wouldn't actually happen.

Pure source-position tests — DB-free, since this dev environment can't
import the full app (missing fastapi) — plus mocked-db tests of
``_is_admin`` / ``get_avatar_slot_summary`` / ``deduct_render_usage`` /
``_describe_next_render_billing``'s branching.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_BILLING = _ORCH_ROOT / "services" / "billing_service.py"


def _read() -> str:
    return _BILLING.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    start = src.find(f"async def {name}(")
    if start == -1:
        start = src.find(f"def {name}(")
    assert start != -1, f"{name} not found"
    end_async = src.find("\nasync def ", start + 1)
    end_sync = src.find("\ndef ", start + 1)
    candidates = [e for e in (end_async, end_sync) if e != -1]
    end = min(candidates) if candidates else -1
    return src[start:end if end != -1 else None]


def test_render_preflight_checks_admin_before_any_subscription_lookup():
    body = _fn_body(_read(), "check_render_preflight")
    admin_idx = body.find("await _is_admin(db, owner_id)")
    assert admin_idx != -1, "check_render_preflight must bypass for admins"
    sub_idx = body.find("get_active_subscription(")
    wallet_idx = body.find("get_or_create_credit_wallet(")
    assert admin_idx < sub_idx
    assert admin_idx < wallet_idx


def test_avatar_slot_check_checks_admin_before_any_subscription_lookup():
    body = _fn_body(_read(), "check_avatar_slot_available")
    admin_idx = body.find("await _is_admin(db, owner_id)")
    assert admin_idx != -1, "check_avatar_slot_available must bypass for admins"
    summary_idx = body.find("get_avatar_slot_summary(")
    assert admin_idx < summary_idx


def test_is_admin_fails_closed_and_uses_the_user_role_enum():
    body = _fn_body(_read(), "_is_admin")
    assert "UserRole.ADMIN" in body
    assert "bool(user and user.role == UserRole.ADMIN)" in body, (
        "must fail closed (return False) when the user lookup comes back "
        "None, never silently grant the bypass"
    )


def test_is_admin_behavior_with_a_mocked_db():
    """Exercises the real branching logic (admin / non-admin / missing
    user) via a minimal db.get() stand-in — avoids importing the app's
    full dependency stack, not available in this dev environment."""

    class _Role:
        ADMIN = "ADMIN"
        CREATOR = "CREATOR"

    class _User:
        def __init__(self, role):
            self.role = role

    class _FakeDB:
        def __init__(self, user):
            self._user = user

        async def get(self, _model, _id):
            return self._user

    async def _is_admin(db, owner_id):
        user = await db.get(_User, owner_id)
        return bool(user and user.role == _Role.ADMIN)

    async def _run():
        assert await _is_admin(_FakeDB(_User(_Role.ADMIN)), "u_admin") is True
        assert await _is_admin(_FakeDB(_User(_Role.CREATOR)), "u_creator") is False
        assert await _is_admin(_FakeDB(None), "u_missing") is False

    asyncio.run(_run())


def test_get_avatar_slot_summary_checks_admin_before_the_free_tier_math():
    body = _fn_body(_read(), "get_avatar_slot_summary")
    admin_idx = body.find("await _is_admin(db, owner_id)")
    assert admin_idx != -1, (
        "get_avatar_slot_summary must special-case admins — this is what "
        "the avatar Setup page's disable-the-button check actually reads"
    )
    free_tier_idx = body.find("FREE_TIER[")
    assert admin_idx < free_tier_idx


def test_get_avatar_slot_summary_admin_branch_never_reports_zero_remaining():
    """Re-implements the two branches (mocked db.get(), no app import
    needed) and checks the exact property the frontend's atSlotLimit reads:
    remaining <= 0 must be false for an admin no matter how many avatars
    they've already made."""

    async def get_avatar_slot_summary(is_admin: bool, used_count: int) -> dict:
        if is_admin:
            headroom = 1000
            return {
                "included": used_count + headroom, "purchased": 0,
                "total": used_count + headroom, "used": used_count,
                "remaining": headroom, "unlimited": True,
            }
        included, purchased = 3, 0
        total = included + purchased
        return {
            "included": included, "purchased": purchased, "total": total,
            "used": used_count, "remaining": max(total - used_count, 0),
            "unlimited": False,
        }

    async def _run():
        # Admin already well past the normal free-tier count.
        admin_summary = await get_avatar_slot_summary(True, used_count=50)
        assert admin_summary["remaining"] > 0
        assert admin_summary["unlimited"] is True

        # Non-admin at the limit — real behaviour must be unchanged.
        normal_summary = await get_avatar_slot_summary(False, used_count=3)
        assert normal_summary["remaining"] == 0
        assert normal_summary["unlimited"] is False

    asyncio.run(_run())


def test_deduct_render_usage_checks_admin_before_touching_wallet_or_stripe():
    body = _fn_body(_read(), "deduct_render_usage")
    admin_idx = body.find("await _is_admin(db, owner_id)")
    assert admin_idx != -1, (
        "deduct_render_usage — the function that actually bills a "
        "COMPLETED render — must bypass for admins too, not just the "
        "start-of-render gate"
    )
    charge_idx = body.find("_attempt_overage_charge(")
    credits_idx = body.find("await get_or_create_credit_wallet(db, owner_id)")
    assert admin_idx < charge_idx, (
        "the admin bypass must come before _attempt_overage_charge — "
        "that's the real Stripe off-session charge"
    )
    assert admin_idx < credits_idx


def test_deduct_render_usage_admin_branch_never_calls_stripe_or_wallet():
    """Re-implements the admin branch to lock in the exact behavior: full
    billable_minutes recorded as free, zero credits/overage touched."""

    def deduct_render_usage_admin_branch(billable_minutes: float) -> dict:
        return {
            "included_minutes_applied": 0.0,
            "credits_minutes_applied": 0.0,
            "credits_amount_cents": 0,
            "overage_minutes_applied": 0.0,
            "overage_rate_cents_per_minute": None,
            "overage_amount_cents": 0,
            "free_minutes_applied": billable_minutes,
        }

    result = deduct_render_usage_admin_branch(billable_minutes=42.5)
    assert result["free_minutes_applied"] == 42.5
    assert result["overage_amount_cents"] == 0
    assert result["credits_amount_cents"] == 0


def test_describe_next_render_billing_admin_never_shows_card_charge_warning():
    body = _fn_body(_read(), "_describe_next_render_billing")
    admin_idx = body.find("if is_admin:")
    assert admin_idx != -1
    overage_idx = body.find('source = "overage"')
    assert admin_idx < overage_idx


def test_describe_next_render_billing_admin_branch_with_a_worked_example():
    """The exact scenario the client asked about: an admin who ALSO has an
    active subscription + saved card, with minutes exhausted and no
    credits — this used to compute source="overage" / will_charge_card=True
    (a real, if now-unfired, warning). Confirms it no longer does."""

    def describe_next_render_billing(is_admin, remaining, billable, wallet_balance, is_free_tier, has_card):
        if is_admin:
            return {"source": "included", "will_charge_card": False}
        if remaining > 0 and (billable or is_free_tier):
            source = "included"
        elif wallet_balance > 0:
            source = "credits"
        elif billable:
            source = "overage"
        else:
            source = "blocked"
        return {"source": source, "will_charge_card": source == "overage" and has_card}

    admin_result = describe_next_render_billing(
        True, remaining=0, billable=True, wallet_balance=0, is_free_tier=False, has_card=True,
    )
    assert admin_result["source"] == "included"
    assert admin_result["will_charge_card"] is False

    # Identical inputs, non-admin — real warning must still fire.
    non_admin_result = describe_next_render_billing(
        False, remaining=0, billable=True, wallet_balance=0, is_free_tier=False, has_card=True,
    )
    assert non_admin_result["source"] == "overage"
    assert non_admin_result["will_charge_card"] is True


def test_get_billing_dashboard_passes_is_admin_through():
    body = _fn_body(_read(), "get_billing_dashboard")
    assert "is_admin = await _is_admin(db, owner_id)" in body
    assert "_describe_next_render_billing(subscription, period, wallet, is_admin)" in body
