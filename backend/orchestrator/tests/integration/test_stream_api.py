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
