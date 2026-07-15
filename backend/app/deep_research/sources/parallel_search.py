from __future__ import annotations
import logging
from typing import Optional

from app.deep_research.config import PARALLEL_API_KEY
from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1/search"


class ParallelSearchSource(BaseSource):
    """Parallel.ai 검색 API — 메인 검색 소스."""

    source_type = "parallel"

    def is_available(self) -> bool:
        return bool(PARALLEL_API_KEY)

    async def search(self, query: str, max_results: int = 10, num_results: int | None = None, **kwargs) -> list[SearchResult]:
        # 파라미터명 표준은 max_results (tavily와 통일).
        # num_results는 하위호환 alias — 이름 불일치로 **kwargs에 빨려 들어가
        # 결과 수 제한이 조용히 무시되던 버그 방지.
        if num_results is not None:
            max_results = num_results
        if not self.is_available():
            logger.warning("[parallel] API 키 없음 — 건너뜀")
            return []
        try:
            async with self._make_client() as client:
                resp = await self._post_with_retry(
                    client,
                    PARALLEL_SEARCH_URL,
                    json={
                        "search_queries": [query],
                        "mode": "advanced",
                        "advanced_settings": {"max_results": max_results},
                    },
                    headers={
                        "x-api-key": PARALLEL_API_KEY,
                        "Content-Type": "application/json",
                    },
                )
                if resp is None or resp.status_code != 200:
                    logger.warning(f"[parallel] 검색 실패: {resp.status_code if resp else 'None'}")
                    return []
                data = resp.json()
                results = []
                # 응답: {"results": [...]} 또는 {"search_results": [...]}
                items = data.get("results") or data.get("search_results") or []
                for item in items:
                    results.append(SearchResult(
                        url=item.get("url", ""),
                        title=item.get("title", ""),
                        content=item.get("content", item.get("excerpt", item.get("snippet", ""))),
                        source_type=self.source_type,
                        relevance_score=item.get("score", 0.0),
                        published_date=item.get("published_date"),
                    ))
                logger.info(f"[parallel] '{query[:50]}' → {len(results)}건")
                return results
        except Exception as e:
            logger.error(f"[parallel] 예외: {e}")
            return []

    async def search_batch(self, queries: list[str], num_results: int = 10) -> list[SearchResult]:
        """여러 쿼리를 한 번의 API 호출로 처리."""
        if not self.is_available() or not queries:
            return []
        try:
            async with self._make_client() as client:
                resp = await self._post_with_retry(
                    client,
                    PARALLEL_SEARCH_URL,
                    json={
                        "search_queries": queries,
                        "mode": "advanced",
                        "advanced_settings": {"max_results": num_results},
                    },
                    headers={
                        "x-api-key": PARALLEL_API_KEY,
                        "Content-Type": "application/json",
                    },
                )
                if resp is None or resp.status_code != 200:
                    logger.warning(f"[parallel/batch] 실패: {resp.status_code if resp else 'None'}")
                    return []
                data = resp.json()
                items = data.get("results") or data.get("search_results") or []
                results = []
                for item in items:
                    results.append(SearchResult(
                        url=item.get("url", ""),
                        title=item.get("title", ""),
                        content=item.get("content", item.get("excerpt", item.get("snippet", ""))),
                        source_type=self.source_type,
                        relevance_score=item.get("score", 0.0),
                        published_date=item.get("published_date"),
                    ))
                logger.info(f"[parallel/batch] {len(queries)}개 쿼리 → {len(results)}건")
                return results
        except Exception as e:
            logger.error(f"[parallel/batch] 예외: {e}")
            return []
