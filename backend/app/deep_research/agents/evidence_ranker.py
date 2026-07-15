"""증거 랭커 — URL·도메인 기반 신뢰도 점수 산정."""
from __future__ import annotations
import logging
import re
from urllib.parse import urlparse

from app.deep_research.models import SearchResult, ExtractedContent, SourceInfo, CredibilityLevel
from app.deep_research.sources.source_registry import (
    get_source_by_domain, get_domain_tier,
)

logger = logging.getLogger(__name__)

# 도메인이 아닌 URL 패턴만 로컬 유지 (레지스트리는 도메인 단위)
_LOW_CRED_URL_RE = re.compile(r"yahoo\.com/finance|rumor|gossip|leaked", re.IGNORECASE)

# tier → (점수, CredibilityLevel) — 신뢰도 자체는 source_registry가 단일 진실 소스
_TIER_SCORE: dict[int, tuple[float, CredibilityLevel]] = {
    1: (1.0, CredibilityLevel.HIGH),
    2: (0.85, CredibilityLevel.HIGH),
    3: (0.65, CredibilityLevel.MEDIUM),
    4: (0.25, CredibilityLevel.LOW),
}


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.removeprefix("www.").lower()
    except Exception:
        return ""


def score_url(url: str) -> tuple[float, CredibilityLevel]:
    """
    URL → (점수 0~1, CredibilityLevel)
    tier1(규제 공시)=1.0 / tier2(공식 거래소·Tier-1 미디어)=0.85 /
    tier3(전문 분석)=0.65 / tier4(자동생성·루머·소셜)=0.25 / 미등록=0.5
    """
    domain = _extract_domain(url)
    if not domain:
        return 0.5, CredibilityLevel.MEDIUM

    official = get_source_by_domain(domain)
    tier = get_domain_tier(domain)
    if tier is not None:
        score, cred = _TIER_SCORE.get(tier, (0.5, CredibilityLevel.MEDIUM))
        if tier == 2 and official is None:
            # Tier-1 '미디어': credibility는 레지스트리와 동일하게 HIGH 유지,
            # 랭킹 점수만 공식 거래소(0.85)보다 한 단계 아래(공식 소스 우선 원칙)
            return 0.75, CredibilityLevel.HIGH
        return score, cred

    # 도메인 레지스트리에 없는 URL 패턴 (rumor/gossip 등)
    if _LOW_CRED_URL_RE.search(url):
        return 0.25, CredibilityLevel.LOW

    return 0.5, CredibilityLevel.MEDIUM


def rank_results(results: list[SearchResult]) -> list[SearchResult]:
    """SearchResult 리스트를 신뢰도 점수 기준 내림차순 정렬."""
    def _key(r: SearchResult) -> float:
        score, _ = score_url(r.url)
        return score * 0.6 + r.relevance_score * 0.4

    return sorted(results, key=_key, reverse=True)


def rank_contents(contents: list[ExtractedContent]) -> list[ExtractedContent]:
    """ExtractedContent 리스트를 URL 신뢰도 기준 정렬."""
    def _key(c: ExtractedContent) -> float:
        score, _ = score_url(c.url)
        return score

    return sorted(contents, key=_key, reverse=True)


def annotate_source_credibility(sources: list[SourceInfo]) -> list[SourceInfo]:
    """SourceInfo 리스트에 credibility를 자동 주입."""
    for src in sources:
        _, cred = score_url(src.url)
        src.credibility = cred
    return sources


class EvidenceRanker:
    score_url = staticmethod(score_url)
    rank_results = staticmethod(rank_results)
    rank_contents = staticmethod(rank_contents)
    annotate_source_credibility = staticmethod(annotate_source_credibility)


evidence_ranker = EvidenceRanker()
