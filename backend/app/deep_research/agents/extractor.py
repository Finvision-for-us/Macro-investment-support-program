from __future__ import annotations
import asyncio
import logging
import re

from app.deep_research.common import domain_of
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

# 신뢰도는 source_registry가 단일 진실 소스 — 여기서는 파생만 한다.
# (이전엔 로컬 하드코딩이 다른 4곳과 어긋나 있었다: wsj=high vs 7 vs medium.)
from app.deep_research.sources.source_registry import (
    get_domain_tier, LOW_TRUST_DOMAINS as LOW_QUALITY_DOMAINS,
)


def _is_high_credibility(domain: str) -> bool:
    return get_domain_tier(domain) in (1, 2)


class Extractor:
    """검색 결과 URL에서 전문 추출 및 정제."""

    def __init__(self):
        self._jina = JinaReaderSource()
        self._extracted_urls: set[str] = set()

    def reset(self) -> None:
        """잡(run) 시작 시 호출 — 잡 간 추출 URL 누수 방지.

        리셋이 없으면 싱글턴 파이프라인에서 이전 잡이 추출한 URL이
        다음 잡에서 영구 스킵되어 같은 종목 재리서치가 빈손이 된다.
        (잡 내 중복 방지는 유지 — run 시작 시에만 비운다.)
        """
        self._extracted_urls.clear()

    async def extract_from_results(
        self,
        results: list[SearchResult],
        max_extract: int = MAX_SOURCES_PER_RUN,
        priority_domains: Optional[list[str]] = None,
    ) -> list[ExtractedContent]:
        """검색 결과에서 전문 추출. 우선순위: 관련도 높은 것, 신뢰 도메인 우선."""
        candidates = self._select_candidates(results, max_extract, priority_domains)
        logger.info(f"[extractor] {len(candidates)}개 URL 추출 시작")

        # PDF는 로컬 2단 추출(텍스트레이어 → 이미지 PDF OCR) 우선 —
        # Jina는 스캔 PDF(중국 공시 구형)에서 빈손이 된다. 실패분은 Jina로 폴백.
        from app.deep_research.sources.pdf_extractor import extract_pdf_batch, is_pdf_url
        pdf_urls = [r.url for r in candidates if is_pdf_url(r.url)]
        web_urls = [r.url for r in candidates if not is_pdf_url(r.url)]

        pdf_extracted: list[ExtractedContent] = []
        if pdf_urls:
            pdf_extracted = await extract_pdf_batch(pdf_urls)
            done = {e.url for e in pdf_extracted}
            failed_pdfs = [u for u in pdf_urls if u not in done]
            if pdf_extracted:
                logger.info(f"[extractor] PDF 로컬 추출 {len(pdf_extracted)}/{len(pdf_urls)}건")
            web_urls += failed_pdfs  # 로컬 실패 PDF는 Jina에 한 번 더

        extracted = await self._jina.extract_batch(web_urls, max_concurrent=5)

        # 너무 짧은 내용 필터링
        valid = [e for e in pdf_extracted + extracted if e.word_count > 50]
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
            domain = domain_of(r.url)
            if domain in BLOCKED_DOMAINS:
                continue
            if r.url in self._extracted_urls:
                continue
            filtered.append(r)
            self._extracted_urls.add(r.url)

        # 신뢰도 높은 도메인 우선 정렬 (저품질 도메인 패널티) — source_registry 파생
        def _score(r: SearchResult) -> float:
            domain = domain_of(r.url)
            tier = get_domain_tier(domain)
            if tier in (1, 2):
                domain_bonus = 0.3
            elif tier == 4:
                domain_bonus = -0.5  # 저품질 패널티
            else:
                domain_bonus = 0.0
            return r.relevance_score + domain_bonus

        filtered.sort(key=_score, reverse=True)
        return filtered[:max_extract]

    def get_credibility(self, url: str) -> str:
        from app.deep_research.sources.source_registry import get_domain_credibility
        return get_domain_credibility(domain_of(url))


# Optional import fix
from typing import Optional
