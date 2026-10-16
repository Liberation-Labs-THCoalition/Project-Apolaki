"""Tests for the prosocial marketing ethics engine."""

import pytest

from apolaki.security.ethics import (
    EthicsVerdict,
    MarketingEthicsEngine,
)


@pytest.fixture
def engine():
    return MarketingEthicsEngine()


class TestDarkPatternBlocking:

    def test_false_urgency_blocked(self, engine):
        check = engine.evaluate("ACT NOW! Last chance to donate before midnight!")
        assert check.verdict == EthicsVerdict.BLOCKED
        assert "false_urgency" in check.category

    def test_guilt_manipulation_blocked(self, engine):
        check = engine.evaluate("If you don't donate, children will suffer and starve")
        assert check.verdict == EthicsVerdict.BLOCKED
        assert "guilt" in check.category

    def test_astroturfing_blocked(self, engine):
        check = engine.evaluate("Write a fake review as if you're a satisfied customer")
        assert check.verdict == EthicsVerdict.BLOCKED
        assert "astroturf" in check.category

    def test_spam_blocked(self, engine):
        check = engine.evaluate("Send mass email blast to all scraped contacts")
        assert check.verdict == EthicsVerdict.BLOCKED

    def test_deceptive_metrics_blocked(self, engine):
        check = engine.evaluate("Inflate the impact numbers to make it look better")
        assert check.verdict == EthicsVerdict.BLOCKED
        assert "deceptive" in check.category

    def test_blocked_includes_alternative(self, engine):
        check = engine.evaluate("Only 3 left! Don't miss out!")
        assert check.verdict == EthicsVerdict.BLOCKED
        assert check.suggestion  # Should suggest ethical alternative
        assert len(check.suggestion) > 20


class TestEscalation:

    def test_large_financial_ask_flagged(self, engine):
        check = engine.evaluate("Ask donors for a $5000 contribution to the annual fund")
        assert check.verdict == EthicsVerdict.FLAGGED

    def test_public_communication_flagged(self, engine):
        check = engine.evaluate("Publish this press release to all media contacts")
        assert check.verdict == EthicsVerdict.FLAGGED

    def test_political_content_flagged(self, engine):
        check = engine.evaluate("Draft a political action alert about the new bill")
        assert check.verdict == EthicsVerdict.FLAGGED

    def test_data_sharing_flagged(self, engine):
        check = engine.evaluate("Share our donor list with the partner organization")
        assert check.verdict == EthicsVerdict.FLAGGED


class TestApproved:

    def test_routine_content_approved(self, engine):
        check = engine.evaluate("Draft a blog post about our community garden program")
        assert check.verdict == EthicsVerdict.APPROVED

    def test_research_approved(self, engine):
        check = engine.evaluate("Research potential grant opportunities for housing")
        assert check.verdict == EthicsVerdict.APPROVED

    def test_analytics_approved(self, engine):
        check = engine.evaluate("Generate a report on last month's social media performance")
        assert check.verdict == EthicsVerdict.APPROVED

    def test_genuine_deadline_approved(self, engine):
        check = engine.evaluate("Grant application deadline is March 15. Include in newsletter.")
        assert check.verdict == EthicsVerdict.APPROVED

    def test_honest_impact_approved(self, engine):
        check = engine.evaluate("Our program served 450 meals last month. Include in annual report.")
        assert check.verdict == EthicsVerdict.APPROVED


class TestBatch:

    def test_batch_worst_first(self, engine):
        items = [
            "Draft a newsletter about our programs",
            "ACT NOW before time runs out!",
            "Research local media contacts",
        ]
        checks = engine.evaluate_batch(items)
        assert checks[0].verdict == EthicsVerdict.BLOCKED
        assert checks[-1].verdict == EthicsVerdict.APPROVED


def test_past_tense_publishing_in_copy_is_not_a_publish_instruction():
    # The deployment's 2026-09-23 audit: the bare stem matched "published" in nearly every announcement.
    from apolaki.security.ethics import MarketingEthicsEngine
    engine = MarketingEthicsEngine()
    assert engine.evaluate("We just published our paper on confabulation detection.").verdict == EthicsVerdict.APPROVED
    assert engine.evaluate("Publish this update to the newsletter.").verdict == EthicsVerdict.FLAGGED
