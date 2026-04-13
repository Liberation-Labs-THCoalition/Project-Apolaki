"""Project Apolaki - Marketing & Fundraising Skill Chips

Central registry for all domain skill chips. Each chip encapsulates
the capabilities, configuration, and ethical constraints for its domain.
"""

from .web_design import WebDesignChip
from .social_media import SocialMediaChip
from .content_creation import ContentCreationChip
from .lead_generation import LeadGenerationChip
from .partnerships import PartnershipsChip
from .media_relations import MediaRelationsChip
from .fundraising import FundraisingChip
from .brand_strategy import BrandStrategyChip
from .analytics import AnalyticsChip
from .community_engagement import CommunityEngagementChip

SKILL_REGISTRY: dict[str, type] = {
    "web_design": WebDesignChip,
    "social_media": SocialMediaChip,
    "content_creation": ContentCreationChip,
    "lead_generation": LeadGenerationChip,
    "partnerships": PartnershipsChip,
    "media_relations": MediaRelationsChip,
    "fundraising": FundraisingChip,
    "brand_strategy": BrandStrategyChip,
    "analytics": AnalyticsChip,
    "community_engagement": CommunityEngagementChip,
}

__all__ = [
    "SKILL_REGISTRY",
    "WebDesignChip",
    "SocialMediaChip",
    "ContentCreationChip",
    "LeadGenerationChip",
    "PartnershipsChip",
    "MediaRelationsChip",
    "FundraisingChip",
    "BrandStrategyChip",
    "AnalyticsChip",
    "CommunityEngagementChip",
]
