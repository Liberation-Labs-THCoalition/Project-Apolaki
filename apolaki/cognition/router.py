"""Keyword-based request router for Apolaki's 10 marketing domains.

Maps incoming user messages to skill domains via keyword matching,
with fast classifier integration for deny/escalate/allow pre-screening.
This is a v1 router -- keyword matching + fast classifier only, no full
EFE orchestration loop (that comes in v2).

Flow::

    message -> _keyword_match() -> FastClassifier.classify()
        FAST_DENY  -> RoutingResult(domain="blocked")
        FAST_ALLOW -> RoutingResult(domain=matched_domain)
        ESCALATED  -> EFE scoring over candidate domains -> RoutingResult
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from apolaki.cognition.efe import (
    ANALYTICS_WEIGHTS,
    BRAND_STRATEGY_WEIGHTS,
    COMMUNITY_WEIGHTS,
    CONTENT_CREATION_WEIGHTS,
    DEFAULT_WEIGHTS,
    EFECalculator,
    EFEScore,
    EFEWeights,
    FUNDRAISING_WEIGHTS,
    LEAD_GENERATION_WEIGHTS,
    MEDIA_RELATIONS_WEIGHTS,
    PARTNERSHIPS_WEIGHTS,
    SOCIAL_MEDIA_WEIGHTS,
    WEB_DESIGN_WEIGHTS,
)
from apolaki.cognition.fast_classifier import (
    ClassificationStage,
    FastClassification,
    FastClassifier,
    FastClassifierConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain-specific EFE weight mapping
# ---------------------------------------------------------------------------

DOMAIN_EFE_WEIGHTS: dict[str, EFEWeights] = {
    "web_design": WEB_DESIGN_WEIGHTS,
    "social_media": SOCIAL_MEDIA_WEIGHTS,
    "content_creation": CONTENT_CREATION_WEIGHTS,
    "lead_generation": LEAD_GENERATION_WEIGHTS,
    "partnerships": PARTNERSHIPS_WEIGHTS,
    "media_relations": MEDIA_RELATIONS_WEIGHTS,
    "fundraising": FUNDRAISING_WEIGHTS,
    "brand_strategy": BRAND_STRATEGY_WEIGHTS,
    "analytics": ANALYTICS_WEIGHTS,
    "community_engagement": COMMUNITY_WEIGHTS,
}


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingResult:
    """Immutable record of a routing outcome."""

    domain: str
    confidence: float
    reasoning: str
    efe_score: Optional[EFEScore] = None


# ---------------------------------------------------------------------------
# Default routing table -- keyword -> domain
# ---------------------------------------------------------------------------

_DEFAULT_ROUTING_TABLE: dict[str, str] = {
    # web_design
    "website": "web_design",
    "landing page": "web_design",
    "seo": "web_design",
    "cms": "web_design",
    "wordpress": "web_design",
    "html": "web_design",
    "css": "web_design",
    "web design": "web_design",
    # social_media
    "social media": "social_media",
    "instagram": "social_media",
    "twitter": "social_media",
    "linkedin": "social_media",
    "tiktok": "social_media",
    "post": "social_media",
    "schedule": "social_media",
    "content calendar": "social_media",
    # content_creation
    "blog": "content_creation",
    "article": "content_creation",
    "newsletter": "content_creation",
    "copy": "content_creation",
    "write": "content_creation",
    "press release": "content_creation",
    "email campaign": "content_creation",
    # lead_generation
    "leads": "lead_generation",
    "prospects": "lead_generation",
    "outreach": "lead_generation",
    "crm": "lead_generation",
    "pipeline": "lead_generation",
    "sales funnel": "lead_generation",
    # partnerships
    "partner": "partnerships",
    "sponsor": "partnerships",
    "alliance": "partnerships",
    "collaboration": "partnerships",
    "joint venture": "partnerships",
    # media_relations
    "press": "media_relations",
    "media": "media_relations",
    "journalist": "media_relations",
    "reporter": "media_relations",
    "interview": "media_relations",
    "story": "media_relations",
    "pitch": "media_relations",
    # fundraising
    "fundraise": "fundraising",
    "donate": "fundraising",
    "grant": "fundraising",
    "campaign": "fundraising",
    "gala": "fundraising",
    "annual fund": "fundraising",
    "major gift": "fundraising",
    # brand_strategy
    "brand": "brand_strategy",
    "positioning": "brand_strategy",
    "messaging": "brand_strategy",
    "logo": "brand_strategy",
    "identity": "brand_strategy",
    "market research": "brand_strategy",
    # analytics
    "analytics": "analytics",
    "metrics": "analytics",
    "roi": "analytics",
    "conversion": "analytics",
    "a/b test": "analytics",
    "dashboard": "analytics",
    "kpi": "analytics",
    # community_engagement
    "community": "community_engagement",
    "engagement": "community_engagement",
    "volunteer": "community_engagement",
    "advocate": "community_engagement",
    "mobilize": "community_engagement",
    "grassroots": "community_engagement",
}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class Router:
    """Keyword-based request router with fast classifier integration.

    Parameters
    ----------
    routing_table:
        ``{keyword: domain}`` map.  Uses the default marketing table
        when *None*.
    fallback_domain:
        Domain returned when no keyword matches.
    confidence_threshold:
        Minimum confidence to accept a keyword match without EFE.
    fast_classifier:
        Optional pre-configured FastClassifier instance.
    efe_calculator:
        Optional EFE calculator for scoring ambiguous requests.
    """

    def __init__(
        self,
        routing_table: dict[str, str] | None = None,
        fallback_domain: str = "general",
        confidence_threshold: float = 0.6,
        fast_classifier: FastClassifier | None = None,
        efe_calculator: EFECalculator | None = None,
    ) -> None:
        self._routing_table = routing_table or dict(_DEFAULT_ROUTING_TABLE)
        self._fallback_domain = fallback_domain
        self._confidence_threshold = confidence_threshold
        self._fast = fast_classifier or FastClassifier()
        self._efe = efe_calculator or EFECalculator()

    # -- public API ---------------------------------------------------------

    def route(self, message: str) -> RoutingResult:
        """Route a message to the best skill domain.

        1. Keyword-match against the routing table.
        2. Fast classifier pre-screens (deny / allow / escalate).
        3. If escalated, use EFE scoring over candidate domains.
        4. Fall back to ``fallback_domain`` if nothing matches.
        """
        domain, confidence, reasoning, candidate_hits = self._keyword_match(
            message
        )

        # Stage 1: Fast classifier pre-screening
        fast_result = self._fast.classify(
            message, domain, confidence, candidate_hits,
        )

        if fast_result.stage == ClassificationStage.FAST_DENY:
            return RoutingResult(
                domain="blocked",
                confidence=0.0,
                reasoning=fast_result.reason,
            )

        if fast_result.stage == ClassificationStage.FAST_ALLOW:
            return RoutingResult(
                domain=fast_result.domain or domain,
                confidence=fast_result.confidence,
                reasoning=fast_result.reason,
            )

        # Stage 2: EFE scoring for escalated requests
        efe_score: EFEScore | None = None
        if len(candidate_hits) > 1 or confidence < self._confidence_threshold:
            efe_score = self._score_candidates_with_efe(
                candidate_hits, confidence
            )
            if efe_score is not None:
                domain = efe_score.policy_id
                reasoning = (
                    f"EFE-selected '{domain}' "
                    f"(total={efe_score.total:.3f}, "
                    f"risk={efe_score.risk_component:.3f}, "
                    f"ambiguity={efe_score.ambiguity_component:.3f}, "
                    f"epistemic={efe_score.epistemic_component:.3f})"
                )
                confidence = max(
                    confidence, 0.5 + 0.3 * (1.0 - max(efe_score.total, 0.0))
                )

        return RoutingResult(
            domain=domain,
            confidence=confidence,
            reasoning=reasoning,
            efe_score=efe_score,
        )

    def register_domain(self, domain: str, keywords: list[str]) -> None:
        """Add or update keywords for *domain* in the routing table."""
        for kw in keywords:
            self._routing_table[kw.lower()] = domain

    def get_routing_table(self) -> dict[str, str]:
        """Return a **copy** of the current routing table."""
        return dict(self._routing_table)

    # -- internals ----------------------------------------------------------

    def _keyword_match(
        self, message: str
    ) -> tuple[str, float, str, dict[str, int]]:
        """Return ``(domain, confidence, reasoning, hits)`` via keyword scan."""
        msg_lower = message.lower()
        hits: dict[str, int] = {}
        for keyword, domain in self._routing_table.items():
            count = len(re.findall(re.escape(keyword), msg_lower))
            if count:
                hits[domain] = hits.get(domain, 0) + count

        if not hits:
            return self._fallback_domain, 0.3, "no keyword match", hits

        best_domain = max(hits, key=hits.__getitem__)
        total_hits = sum(hits.values())
        confidence = min(0.95, 0.5 + 0.1 * hits[best_domain])
        reasoning = (
            f"keyword match: {hits[best_domain]}/{total_hits} hits "
            f"for '{best_domain}'"
        )
        return best_domain, confidence, reasoning, hits

    def _score_candidates_with_efe(
        self,
        candidate_hits: dict[str, int],
        keyword_confidence: float,
    ) -> EFEScore | None:
        """Score candidate domains with EFE and return the best.

        Maps keyword-match confidence to ``uncertainty`` and domain
        specificity (hit count / total) to ``information_gain``.
        Applies domain-specific EFE weight profiles per candidate.
        """
        if not candidate_hits:
            return None

        total_hits = max(sum(candidate_hits.values()), 1)
        uncertainty = 1.0 - keyword_confidence

        scores: list[EFEScore] = []
        for domain, hit_count in candidate_hits.items():
            weights = DOMAIN_EFE_WEIGHTS.get(domain, DEFAULT_WEIGHTS)
            information_gain = hit_count / total_hits

            predicted = {
                "relevance": information_gain,
                "specificity": hit_count / total_hits,
            }
            desired = {"relevance": 1.0, "specificity": 1.0}

            score = self._efe.calculate_efe(
                policy_id=domain,
                predicted_outcome=predicted,
                desired_outcome=desired,
                uncertainty=uncertainty,
                information_gain=information_gain,
                weights=weights,
            )
            scores.append(score)

        return self._efe.select_policy(scores)
