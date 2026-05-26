from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import urlparse

from app.deep_research.config import MAX_SOURCES_PER_RUN
from app.deep_research.models import SearchResult, ExtractedContent
from app.deep_research.sources.jina_reader import JinaReaderSource

logger = logging.getLogger(__name__)

# 추출 가치 없는 도메인 (로그인 필요, 페이월 등)
BLOCKED_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "instagram.com",
    "linkedin.com", "reddit.com", "youtube.com",
    "wsj.com", "ft.com", "bloomberg.com",  # 페이월
}

# 신뢰도 높은 도메인
HIGH_CREDIBILITY_DOMAINS = {
    "sec.gov", "dart.fss.or.kr", "fred.stlouisfed.org", "arxiv.org",
    "reuters.com", "apnews.com", "wsj.com", "ft.com", "bloomberg.com",
    "techcrunch.com", "cnbc.com", "marketwatch.com",
    "szse.cn", "szse.com.cn", "sse.com.cn", "csrc.gov.cn",
    "fsc.go.kr", "ec.europa.eu", "nikkei.com",
}

# 자동생성/저품질 도메인 (점수 패널티 적용, 추출 제한)
LOW_QUALITY_DOMAINS = {
    "stockinsights.ai", "pitchgrade.com", "simplywall.st",
    "wisesheets.io", "stockstory.org",
}


class Extractor:
    """검색 결과 URL에서 전문 추출 및 정제."""

    def __init__(self):
        self._jina = JinaReaderSource()
        self._extracted_urls: set[str] = set()

    async def extract_from_results(
        self,
        results: list[SearchResult],
        max_extract: int = MAX_SOURCES_PER_RUN,
        priority_domains: Optional[list[str]] = None,
    ) -> list[ExtractedContent]:
        """검색 결과에서 전문 추출. 우선순위: 관련도 높은 것, 신뢰 도메인 우선."""
        candidates = self._select_candidates(results, max_extract, priority_domains)
        logger.info(f"[extractor] {len(candidates)}개 URL 추출 시작")

        extracted = await self._jina.extract_batch(
            [r.url for r in candidates],
            max_concurrent=5,
        )

        # 너무 짧은 내용 필터링
        valid = [e for e in extracted if e.word_count > 50]
        logger.info(f"[extractor] {len(valid)}개 전문 추출 완료")
        return valid

    def _select_candidates(
        self,
        results: list[SearchResult],
        max_extract: int,
        priority_domains: Optional[list[str]],
    ) -> list[SearchResult]:
        """추출할 URL 선별."""
        filtered = []
        for r in results:
            if not r.url or not r.url.startswith("http"):
                continue
            domain = urlparse(r.url).netloc.lstrip("www.")
            if domain in BLOCKED_DOMAINS:
                continue
            if r.url in self._extracted_urls:
                continue
            filtered.append(r)
            self._extracted_urls.add(r.url)

        # 신뢰도 높은 도메인 우선 정렬 (저품질 도메인 패널티)
        def _score(r: SearchResult) -> float:
            domain = urlparse(r.url).netloc.lstrip("www.")
            if domain in HIGH_CREDIBILITY_DOMAINS or any(d in domain for d in HIGH_CREDIBILITY_DOMAINS):
                domain_bonus = 0.3
            elif any(d in domain for d in LOW_QUALITY_DOMAINS):
                domain_bonus = -0.5  # 저품질 패널티
            else:
                domain_bonus = 0.0
            return r.relevance_score + domain_bonus

        filtered.sort(key=_score, reverse=True)
        return filtered[:max_extract]

    def get_credibility(self, url: str) -> str:
        domain = urlparse(url).netloc.lstrip("www.")
        if domain in HIGH_CREDIBILITY_DOMAINS or any(d in domain for d in ["gov", "edu", "ac."]):
            return "high"
        if any(d in domain for d in LOW_QUALITY_DOMAINS):
            return "low"
        return "medium"


# Optional import fix
from typing import Optional
