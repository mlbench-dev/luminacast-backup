"""
Chat message classifier — keyword matching, product rules, LLM routing, safety.
"""
import pytest


class TestKeywordMatching:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_shipping_question(self):
        """'how much is shipping' → Tier 1 keyword match."""
        assert False, "SKELETON — implement classifier"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_price_question(self):
        """'what's the price' → Tier 1 keyword match."""
        assert False, "SKELETON — implement classifier"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_case_insensitive(self):
        """'HOW MUCH SHIPPING?' matches same as lowercase."""
        assert False, "SKELETON — implement classifier"


class TestProductRules:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_matches_product_specific_qa(self):
        """'is this for oily skin' matches product rule → Tier 2."""
        assert False, "SKELETON — implement classifier"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_no_match_falls_to_llm(self):
        """Unknown question → Tier 3 LLM fallback."""
        assert False, "SKELETON — implement classifier"


class TestPurchaseDetection:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_purchase_event_detected(self):
        """'🛒 @user bought Product!' flagged as purchase."""
        assert False, "SKELETON — implement classifier"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_normal_message_not_purchase(self):
        """Regular question is NOT flagged as purchase."""
        assert False, "SKELETON — implement classifier"


class TestSafety:

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_response_under_280_chars(self):
        """All responses <= 280 characters."""
        assert False, "SKELETON — implement with LLM response validation"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_no_medical_claims(self):
        """Response never contains 'cures', 'treats', 'heals'."""
        assert False, "SKELETON — implement safety filter"

    @pytest.mark.skip(reason="Skeleton test — not yet implemented")
    def test_abusive_message_ignored(self):
        """Hateful messages get no response."""
        assert False, "SKELETON — implement abuse detection"
