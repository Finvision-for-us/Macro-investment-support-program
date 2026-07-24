from __future__ import annotations
import asyncio
import logging
import time
from collections import defaultdict

from app.deep_research.config import MAX_SEARCH_QUERIES_PER_RUN
from app.deep_research.models import ResearchPlan, SearchResult, SubQuery, SearchAttempt
from app.deep_research.sources.grounding_search import GroundingSearchSource
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
            "grounding": GroundingSearchSource(),
            "sec": SecEdgarSource(),
            "dart": DartSource(),
            "fred": FredSource(),
            "arxiv": ArxivSource(),
        }
        self._total_queries: int = 0
        self._url_seen: set[str] = set()
        self._attempts: list[SearchAttempt] = []

    def reset(self) -> None:
        """잡(run) 시작 시 호출 — 잡 간 상태 누수 방지.

        싱글턴 파이프라인에서 리셋이 없으면: _url_seen에 걸려 두 번째 리서치가
        같은 종목의 핵심 출처를 전부 건너뛰고, _total_queries 누적으로
        MAX_SEARCH_QUERIES_PER_RUN이 '프로세스 수명 총량'이 되어
        이후 모든 검색이 조용히 빈 리스트를 반환한다.
        """
        self._total_queries = 0
        self._url_seen.clear()
        self._attempts.clear()

    @property
    def total_queries(self) -> int:
        return self._total_queries

    @property
    def attempts(self) -> list[SearchAttempt]:
        return list(self._attempts)

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
            self._record_unavailable(sq, available)
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
            self._record_unavailable(sq, available)
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

        # 그라운딩(구글 검색 접지)은 커버리지가 가장 넓은 엔진 — 계획이 명시하지
        # 않아도 모든 쿼리에 병행한다(타 엔진 크레딧 소진 시에도 검색이 살아있게)
        if "grounding" not in sources and self._sources["grounding"].is_available():
            sources = sources + ["grounding"]

        self._total_queries += len(sources)
        tasks = [
            self._search_source(sq.query, s)
            for s in sources if s in self._sources
        ]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[SearchResult] = []
        for r in results_nested:
            if isinstance(r, list):
                combined.extend(r)
        return combined

    def _record_unavailable(self, sq: SubQuery, available: set[str]) -> None:
        for source_name in sq.sources:
            if source_name in self._sources and source_name not in available:
                self._attempts.append(SearchAttempt(
                    query=sq.query,
                    source=source_name,
                    status="not_searched",
                    error_type="source_unavailable",
                    message="API 키 또는 필수 설정 없음",
                ))

    async def _search_source(self, query: str, source_name: str) -> list[SearchResult]:
        """제공자 호출과 상태 기록. 반환 목록 계약은 기존과 동일."""
        started = time.perf_counter()
        status = "provider_error"
        error_type = None
        message = ""
        results: list[SearchResult] = []
        try:
            results = await self._sources[source_name].search(query)
            status = "success" if results else "no_results"
            return results
        except asyncio.TimeoutError as e:
            status, error_type, message = "timeout", "timeout", str(e)
            return []
        except PermissionError as e:
            status, error_type, message = "access_denied", "access_denied", str(e)
            return []
        except (ValueError, TypeError, KeyError) as e:
            status, error_type, message = "parse_failed", "parse_failed", str(e)
            return []
        except Exception as e:
            text = str(e).lower()
            if any(token in text for token in ("403", "401", "access denied", "forbidden")):
                status, error_type = "access_denied", "access_denied"
            elif any(token in text for token in ("timeout", "timed out")):
                status, error_type = "timeout", "timeout"
            else:
                status, error_type = "provider_error", type(e).__name__
            message = str(e)
            return []
        finally:
            self._attempts.append(SearchAttempt(
                query=query,
                source=source_name,
                status=status,
                result_count=len(results),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type=error_type,
                message=message[:300],
            ))

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
