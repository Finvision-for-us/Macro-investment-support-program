"""공식 소스 검색기 — site: 쿼리 기반으로 국가별 공식 소스에서 직접 수집."""
from __future__ import annotations
import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from app.deep_research.agents.jurisdiction_detector import JurisdictionResult
from app.deep_research.agents.multilingual_query_builder import (
    MultilingualQueryBuilder, LocalizedQuery,
)
from app.deep_research.agents.evidence_ranker import EvidenceRanker
from app.deep_research.models import SearchResult
from app.deep_research.sources.source_registry import get_sources_for_country

logger = logging.getLogger(__name__)

_qbuilder = MultilingualQueryBuilder()
_ranker = EvidenceRanker()


class OfficialSourceSearcher:

    def __init__(self, tavily_source=None, parallel_source=None):
        self._tavily = tavily_source
        self._parallel = parallel_source
        # 마지막 search() 호출 통계 (build_coverage_info에서 참조)
        self._last_searched_domains: set[str] = set()
        self._last_query_count: int = 0

    def set_sources(self, tavily_source=None, parallel_source=None):
        self._tavily = tavily_source
        self._parallel = parallel_source

    async def search(
        self,
        original_query: str,
        jurisdiction: JurisdictionResult,
        max_results_per_query: int = 5,
        context: Optional[dict] = None,
    ) -> list[SearchResult]:
        ml_queries = _qbuilder.build(original_query, jurisdiction, context)

        site_queries = [q for q in ml_queries.queries if q.query_type == "official_site"]
        local_queries = [q for q in ml_queries.queries if q.query_type == "local_language"]
        selected = site_queries[:6] + local_queries[:2]

        # 어떤 도메인을 검색했는지 기록
        self._last_searched_domains = {q.site_domain for q in selected if q.site_domain}
        self._last_query_count = len(selected)

        if not selected:
            return []

        raw_results = await asyncio.gather(
            *[self._run_single_query(lq, max_results_per_query) for lq in selected],
            return_exceptions=True,
        )

        all_results: list[SearchResult] = []
        for r in raw_results:
            if isinstance(r, Exception):
                logger.warning(f"[official_searcher] 쿼리 실패 (계속): {r}")
                continue
            all_results.extend(r)

        # 중복 URL 제거
        seen: set[str] = set()
        unique = [r for r in all_results if not (r.url in seen or seen.add(r.url))]

        ranked = _ranker.rank_results(unique)
        logger.info(
            f"[official_searcher] {len(ranked)}개 결과 "
            f"(primary={jurisdiction.primary}, queries={len(selected)}, "
            f"domains={len(self._last_searched_domains)})"
        )
        return ranked

    async def _run_single_query(
        self, lq: LocalizedQuery, max_results: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        if self._tavily:
            try:
                raw = await self._tavily.search(lq.query, num_results=max_results)
                for r in raw:
                    r.source_type = "official"
                results.extend(raw)
            except Exception as e:
                logger.debug(f"[official_searcher] tavily 실패 ({lq.query[:40]!r}): {e}")

        if not results and self._parallel:
            try:
                raw = await self._parallel.search(lq.query, num_results=max_results)
                for r in raw:
                    r.source_type = "official"
                results.extend(raw)
            except Exception as e:
                logger.debug(f"[official_searcher] parallel 실패 ({lq.query[:40]!r}): {e}")

        return results

    def build_coverage_info(
        self,
        jurisdiction: JurisdictionResult,
        collected_urls: list[str],
        official_extracted_count: int = 0,
    ) -> dict:
        all_countries = [jurisdiction.primary] + list(jurisdiction.secondary)
        expected_domains = [
            s.domain
            for country in all_countries
            for s in get_sources_for_country(country)
            if s.tier == 1
        ]

        # 수집된 URL에서 도메인 추출
        collected_domains: set[str] = set()
        for url in collected_urls:
            try:
                d = urlparse(url).netloc.lstrip("www.").lower()
                collected_domains.add(d)
            except Exception:
                pass

        checked: list[str] = []
        unchecked: list[str] = []
        for domain in expected_domains:
            found = any(
                cd == domain or cd.endswith("." + domain)
                for cd in collected_domains
            )
            if found:
                checked.append(domain)
            else:
                was_searched = domain in self._last_searched_domains
                label = f"{domain} (searched, no result)" if was_searched else domain
                unchecked.append(label)

        return {
            "checked": checked,
            "unchecked": unchecked,
            "notes": (
                f"관할: {', '.join(all_countries)} | "
                f"공식 쿼리 {self._last_query_count}개 | "
                f"공식 추출 {official_extracted_count}개 | "
                f"확인 {len(checked)}/{len(expected_domains)} tier-1 소스"
            ),
        }


official_source_searcher = OfficialSourceSearcher()
