"""
AFK monitoring — timers, escalation, overlay triggers.
"""
import pytest


class TestAFKTimer:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_starts_on_product_block(self):
        """Timer starts when product block plays."""
        assert False, "SKELETON — implement AFKMonitor"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_no_timer_on_intro_block(self):
        """Timer does NOT start for non-product blocks."""
        assert False, "SKELETON — implement AFKMonitor"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_cancel_on_pin_confirm(self):
        """Timer cancels when creator confirms pin."""
        assert False, "SKELETON — implement AFKMonitor"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_afk_fires_after_timeout(self):
        """AFK event triggers when timer reaches 0."""
        assert False, "SKELETON — implement AFKMonitor"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_custom_timeout_per_block(self):
        """Different blocks can have different timeouts."""
        assert False, "SKELETON — implement AFKMonitor"


class TestAFKEscalation:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_1_afk_is_overlay_only(self):
        """First AFK = overlay + chat CTA."""
        assert False, "SKELETON — implement escalation"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_5_consecutive_suggests_pause(self):
        """5 consecutive AFKs → suggest pause."""
        assert False, "SKELETON — implement escalation"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_10_consecutive_auto_idle(self):
        """10+ AFKs → switch to idle loop."""
        assert False, "SKELETON — implement escalation"
