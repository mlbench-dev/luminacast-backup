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
