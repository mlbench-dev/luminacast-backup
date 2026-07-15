"""
Billing calculations — Cast pricing, streaming cost, regen cost.
"""
import pytest


class TestCastCreationFee:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_standard_fee_is_1499_cents(self):
        """Standard Cast = $14.99 (1499 cents)."""
        # fee = calculate_cast_creation_fee()
        # assert fee == 1499
        assert False, "SKELETON — implement billing module"


class TestStreamingCost:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_cost_per_minute_is_3_cents(self):
        """$0.03/min = 3 cents/min."""
        # cost = calculate_streaming_cost(minutes=180)
        # assert cost == 540  # cents
        assert False, "SKELETON — implement billing module"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_partial_minutes_rounded_up(self):
        """10.5 minutes billed as 11 minutes."""
        assert False, "SKELETON — implement billing module"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_zero_minutes_costs_zero(self):
        """0 minutes = $0."""
        assert False, "SKELETON — implement billing module"


class TestClipRegenCost:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_single_clip_is_99_cents(self):
        """1 clip regen = $0.99 (99 cents)."""
        assert False, "SKELETON — implement billing module"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_multiple_clips(self):
        """3 clips = $2.97 (297 cents)."""
        assert False, "SKELETON — implement billing module"


class TestAvatarSetupCost:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_setup_is_999_cents(self):
        """Avatar setup = $9.99 (999 cents)."""
        assert False, "SKELETON — implement billing module"
