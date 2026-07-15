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
