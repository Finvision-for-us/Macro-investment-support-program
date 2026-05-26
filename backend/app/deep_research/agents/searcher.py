from __future__ import annotations
import asyncio
import logging
import time
from collections import defaultdict

from app.deep_research.config import MAX_SEARCH_QUERIES_PER_RUN
from app.deep_research.models import ResearchPlan, SearchResult, SubQuery
from app.deep_research.sources.parallel_search import ParallelSearchSource
from app.deep_research.sources.tavily_search import TavilySearchSource
from app.deep_research.sources.sec_edgar import SecEdgarSource
from app.deep_research.sources.dart import DartSource
from app.deep_research.sources.fred import FredSource
from app.deep_research.sources.arxiv import ArxivSource

logger = logging.getLogger(__name__)


class Searcher:
    """다중 소스 병렬 검색 오케스트레이터."""

    def __init__(self):
        self._sources = {
            "parallel": ParallelSearchSource(),
            "tavily": TavilySearchSource(),
            "sec": SecEdgarSource(),
            "dart": DartSource(),
            "fred": FredSource(),
            "arxiv": ArxivSource(),
        }
        self._total_queries: int = 0
        self._url_seen: set[str] = set()

    @property
    def total_queries(self) -> int:
        return self._total_queries

    def get_available_sources(self) -> list[str]:
        return [name for name, src in self._sources.items() if src.is_available()]

    async def search_plan(
        self,
        plan: ResearchPlan,
        priority_filter: Optional[int] = None,
    ) -> list[SearchResult]:
        """계획의 모든 쿼리를 병렬 실행."""
        queries = plan.sub_queries
        if priority_filter is not None:
            queries = [q for q in queries if q.priority <= priority_filter]

        available = set(self.get_available_sources())
        tasks = []
        for sq in queries:
            sources = [s for s in sq.sources if s in available] or list(available)
            tasks.append(self._search_one(sq, sources))

        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        all_results: list[SearchResult] = []
        for r in results_nested:
            if isinstance(r, list):
                all_results.extend(r)
        return self._deduplicate(all_results)

    async def search_queries(self, sub_queries: list[SubQuery]) -> list[SearchResult]:
        """추가 쿼리들 검색 (Critic이 요청한 보완 쿼리)."""
        available = set(self.get_available_sources())
        tasks = []
        for sq in sub_queries:
            if self._total_queries >= MAX_SEARCH_QUERIES_PER_RUN:
                logger.warning("[searcher] 최대 쿼리 수 도달")
                break
            sources = [s for s in sq.sources if s in available] or list(available)
            tasks.append(self._search_one(sq, sources))

        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        all_results: list[SearchResult] = []
        for r in results_nested:
            if isinstance(r, list):
                all_results.extend(r)
        return self._deduplicate(all_results)

    async def _search_one(self, sq: SubQuery, sources: list[str]) -> list[SearchResult]:
        """단일 쿼리를 여러 소스에서 병렬 검색."""
        if self._total_queries >= MAX_SEARCH_QUERIES_PER_RUN:
            return []

        self._total_queries += len(sources)
        tasks = [self._sources[s].search(sq.query) for s in sources if s in self._sources]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[SearchResult] = []
        for r in results_nested:
            if isinstance(r, list):
                combined.extend(r)
        return combined

    def _deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """URL 기반 중복 제거, 관련도 높은 것 우선."""
        unique: list[SearchResult] = []
        for r in sorted(results, key=lambda x: x.relevance_score, reverse=True):
            if r.url and r.url not in self._url_seen:
                self._url_seen.add(r.url)
                unique.append(r)
        return unique


# Optional import fix
from typing import Optional
