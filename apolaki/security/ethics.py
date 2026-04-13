"""Prosocial marketing ethics guardrails.

Marketing is power. This module ensures the power serves the Cause.

The line between "effective marketing" and "manipulation" is where
the ethics engine lives. Effective marketing connects people with
things that genuinely serve them. Manipulation exploits psychology
to extract behavior that serves the marketer at the audience's expense.

Apolaki only does the first kind.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class EthicsVerdict(str, Enum):
    APPROVED = "approved"
    FLAGGED = "flagged"       # Needs human review
    BLOCKED = "blocked"       # Hard deny


@dataclass(frozen=True)
class EthicsCheck:
    """Result of an ethics evaluation."""
    verdict: EthicsVerdict
    reason: str
    category: str = ""        # Which ethics rule triggered
    suggestion: str = ""      # How to fix if flagged


# ---------------------------------------------------------------------------
# Dark pattern detection
# ---------------------------------------------------------------------------

_DARK_PATTERNS = {
    "false_urgency": [
        r"(last\s+chance|act\s+now|don't\s+miss\s+out|expires?\s+soon)",
        r"(only\s+\d+\s+left|limited\s+time|hurry|before\s+it's\s+too\s+late)",
        r"(offer\s+ends|countdown|ticking\s+clock|midnight\s+tonight)",
    ],
    "guilt_manipulation": [
        r"(if\s+you\s+don't\s+(donate|help|act).*\b(suffer|die|starve))",
        r"(how\s+can\s+you\s+(sleep|live).*knowing)",
        r"(their\s+blood\s+is\s+on\s+your\s+hands)",
        r"(you're\s+the\s+only\s+one\s+who\s+can)",
    ],
    "astroturfing": [
        r"(fake\s+(review|testimonial|endorsement|grassroots))",
        r"(sock\s+puppet|astroturf|fake\s+account|impersonat)",
        r"(write\b.*\bas\s+if\s+you're\s+a\b.*\b(customer|user|supporter|donor|member))",
        r"(write\b.*\btestimonial\b.*\bas\s+if)",
        r"(pretend\s+to\s+be\s+a\s+(customer|user|supporter|donor))",
    ],
    "spam": [
        r"(mass\s+email|blast\s+to\s+all|email\s+bomb|spam)",
        r"(scrape\s+(email|contact|profile)s?)",
        r"(buy\s+(email\s+)?list|purchased\s+contacts?)",
        r"(unsolicited\s+(mass|bulk))",
    ],
    "deceptive_metrics": [
        r"(inflat|fake|fabricat)\w*\b.*\b(metric|number|stat|figure|impact)",
        r"(overstat|exaggerat)\w*\b.*\b(impact|result|outcome|reach)",
        r"(misleading\s+(graph|chart|statistic))",
    ],
}

_COMPILED_DARK_PATTERNS = {
    cat: [re.compile(p, re.IGNORECASE) for p in patterns]
    for cat, patterns in _DARK_PATTERNS.items()
}


# ---------------------------------------------------------------------------
# Escalation triggers (human review required)
# ---------------------------------------------------------------------------

_ESCALATION_TRIGGERS = {
    "financial_ask": [
        r"(ask\w*\s+(for\s+)?(a\s+)?(\w+\s+)?(donation|contribution|gift|pledge))",
        r"\$\d{4,}",  # Dollar amounts >= $1000
        r"(major\s+gift|planned\s+giving|legacy\s+gift)",
        r"(fundraising\s+appeal|solicit\w*\s+(fund|donat|gift|support))",
    ],
    "public_communication": [
        r"(publish|post\s+as|send\s+on\s+behalf|release\s+to\s+media)",
        r"(official\s+statement|press\s+release|media\s+advisory)",
        r"(go\s+live|launch\s+campaign|push\s+to\s+production)",
    ],
    "sensitive_topic": [
        r"(political\s+action|endorse\s+candidate|campaign\s+contribution)",
        r"(protest|boycott|strike|demonstration)",
        r"(legal\s+action|lawsuit|litigation|cease\s+and\s+desist)",
    ],
    "data_sharing": [
        r"(share\b.*\b(data|list|contacts?)\b.*\bwith)",
        r"(export\b.*\b(donor|supporter|member)\b.*\b(data|list|info))",
        r"(third.?party\s+(access|sharing|integration))",
    ],
}

_COMPILED_ESCALATION = {
    cat: [re.compile(p, re.IGNORECASE) for p in patterns]
    for cat, patterns in _ESCALATION_TRIGGERS.items()
}


# ---------------------------------------------------------------------------
# Ethics Engine
# ---------------------------------------------------------------------------


class MarketingEthicsEngine:
    """Evaluates marketing content and actions against prosocial ethics.

    Three verdicts:
      APPROVED — action is ethical and can proceed
      FLAGGED  — action needs human review before execution
      BLOCKED  — action violates ethics and is denied

    Usage::

        engine = MarketingEthicsEngine()
        check = engine.evaluate("Send urgent donation appeal to all contacts")
        if check.verdict == EthicsVerdict.BLOCKED:
            print(f"Blocked: {check.reason}")
    """

    def evaluate(self, content: str) -> EthicsCheck:
        """Evaluate content or action against marketing ethics."""

        # Check dark patterns first (hard deny)
        for category, patterns in _COMPILED_DARK_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(content):
                    logger.warning(
                        "Ethics BLOCKED: dark pattern '%s' detected", category
                    )
                    return EthicsCheck(
                        verdict=EthicsVerdict.BLOCKED,
                        reason=f"Dark pattern detected: {category}",
                        category=category,
                        suggestion=self._suggest_alternative(category),
                    )

        # Check escalation triggers (human review)
        for category, patterns in _COMPILED_ESCALATION.items():
            for pattern in patterns:
                if pattern.search(content):
                    logger.info(
                        "Ethics FLAGGED: escalation trigger '%s'", category
                    )
                    return EthicsCheck(
                        verdict=EthicsVerdict.FLAGGED,
                        reason=f"Requires human review: {category}",
                        category=category,
                        suggestion=f"Draft the {category} content and present for human approval before sending.",
                    )

        return EthicsCheck(
            verdict=EthicsVerdict.APPROVED,
            reason="No ethics concerns detected",
        )

    def evaluate_batch(self, items: list[str]) -> list[EthicsCheck]:
        """Evaluate multiple items, return worst verdict first."""
        checks = [self.evaluate(item) for item in items]
        # Sort: blocked first, flagged second, approved last
        priority = {EthicsVerdict.BLOCKED: 0, EthicsVerdict.FLAGGED: 1, EthicsVerdict.APPROVED: 2}
        return sorted(checks, key=lambda c: priority[c.verdict])

    @staticmethod
    def _suggest_alternative(category: str) -> str:
        """Suggest ethical alternatives for blocked patterns."""
        alternatives = {
            "false_urgency": "Use genuine deadlines (grant closing dates, event dates) instead of manufactured urgency. State facts: 'Applications close March 15' not 'ACT NOW OR MISS OUT.'",
            "guilt_manipulation": "Lead with impact and hope, not guilt. Show what donations ENABLE, not what happens without them. 'Your $50 provides meals for a family for a week' not 'Children will starve without your help.'",
            "astroturfing": "Use genuine testimonials from real supporters. Ask permission to share their stories. Authentic voices are more powerful than fabricated ones.",
            "spam": "Build opt-in lists through genuine value (newsletters, resources, events). Quality > quantity. One engaged supporter is worth 100 spam recipients.",
            "deceptive_metrics": "Report accurate metrics with context. If reach was 10,000, say 10,000 — not 'tens of thousands.' Honest metrics build trust that compounds.",
        }
        return alternatives.get(category, "Consider the ethical implications and revise with prosocial intent.")
