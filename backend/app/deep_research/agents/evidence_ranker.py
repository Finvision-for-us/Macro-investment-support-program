"""증거 랭커 — URL·도메인 기반 신뢰도 점수 산정."""
from __future__ import annotations
import logging
import re
from urllib.parse import urlparse

from app.deep_research.models import SearchResult, ExtractedContent, SourceInfo, CredibilityLevel
from app.deep_research.sources.source_registry import get_source_by_domain, ALL_SOURCES

logger = logging.getLogger(__name__)

# tier → credibility 매핑
_TIER_TO_CRED: dict[int, CredibilityLevel] = {
    1: CredibilityLevel.HIGH,
    2: CredibilityLevel.HIGH,
    3: CredibilityLevel.MEDIUM,
}

# 공식 소스 도메인 → tier 캐시
_OFFICIAL_DOMAIN_TIERS: dict[str, int] = {s.domain: s.tier for s in ALL_SOURCES}

# 항상 낮은 신뢰도 도메인 (소셜미디어, 루머 사이트 등)
_LOW_CRED_PATTERNS: list[str] = [
    r"reddit\.com", r"twitter\.com", r"x\.com", r"facebook\.com",
    r"stocktwits", r"seekingalpha", r"motleyfool", r"investopedia",
    r"thestreet", r"benzinga", r"yahoo\.com/finance",
    r"rumor|gossip|leaked",
]
_LOW_CRED_RE = re.compile("|".join(_LOW_CRED_PATTERNS), re.IGNORECASE)

# 중간 신뢰도: 주요 언론사
_MED_CRED_PATTERNS: list[str] = [
    r"reuters\.com", r"bloomberg\.com", r"ft\.com", r"wsj\.com",
    r"nytimes\.com", r"apnews\.com", r"afp\.com",
    r"bbc\.com", r"cnbc\.com", r"marketwatch\.com",
    r"caixin\.com", r"scmp\.com",
    r"yonhapnews\.co\.kr", r"yna\.co\.kr",
    r"nikkei\.com",
]
_MED_CRED_RE = re.compile("|".join(_MED_CRED_PATTERNS), re.IGNORECASE)


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lstrip("www.").lower()
    except Exception:
        return ""


def score_url(url: str) -> tuple[float, CredibilityLevel]:
    """
    URL → (점수 0~1, CredibilityLevel)
    공식 tier-1 = 1.0, tier-2 = 0.85, 주요 언론 = 0.65, 일반 = 0.5, 저신뢰 = 0.25
    """
    domain = _extract_domain(url)
    if not domain:
        return 0.5, CredibilityLevel.MEDIUM

    # 공식 소스 체크
    official = get_source_by_domain(domain)
    if official:
        score = 1.0 if official.tier == 1 else 0.85
        return score, _TIER_TO_CRED.get(official.tier, CredibilityLevel.MEDIUM)

    # 서브도메인 포함 체크 (예: edgar.sec.gov)
    for od, tier in _OFFICIAL_DOMAIN_TIERS.items():
        if domain.endswith("." + od) or domain == od:
            score = 1.0 if tier == 1 else 0.85
            return score, _TIER_TO_CRED.get(tier, CredibilityLevel.MEDIUM)

    # 저신뢰 패턴
    if _LOW_CRED_RE.search(url):
        return 0.25, CredibilityLevel.LOW

    # 주요 언론
    if _MED_CRED_RE.search(url):
        return 0.65, CredibilityLevel.MEDIUM

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
