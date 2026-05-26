from __future__ import annotations
import logging

from app.deep_research.config import FRED_API_KEY
from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred"


class FredSource(BaseSource):
    """FRED API — 미국 거시경제 지표 (연준 데이터)."""

    source_type = "fred"

    def is_available(self) -> bool:
        return bool(FRED_API_KEY)

    async def search(self, query: str, num_results: int = 10, **kwargs) -> list[SearchResult]:
        if not self.is_available():
            logger.warning("[fred] API 키 없음 — 건너뜀")
            return []
        try:
            async with self._make_client() as client:
                params = {
                    "api_key": FRED_API_KEY,
                    "search_text": query,
                    "limit": num_results,
                    "file_type": "json",
                    "order_by": "popularity",
                    "sort_order": "desc",
                }
                resp = await self._get_with_retry(
                    client,
                    f"{FRED_BASE_URL}/series/search",
                    params=params,
                )
                if resp is None or resp.status_code != 200:
                    return []
                data = resp.json()
                results = []
                for series in data.get("seriess", [])[:num_results]:
                    series_id = series.get("id", "")
                    url = f"https://fred.stlouisfed.org/series/{series_id}"
                    results.append(SearchResult(
                        url=url,
                        title=series.get("title", ""),
                        content=(
                            f"{series.get('title', '')} | "
                            f"최신: {series.get('observation_end', '')} | "
                            f"빈도: {series.get('frequency', '')} | "
                            f"단위: {series.get('units', '')}"
                        ),
                        source_type=self.source_type,
                        relevance_score=series.get("popularity", 0) / 100.0,
                        published_date=series.get("observation_end"),
                    ))
                logger.info(f"[fred] '{query[:50]}' → {len(results)}건")
                return results
        except Exception as e:
            logger.error(f"[fred] 예외: {e}")
            return []

    async def get_observations(self, series_id: str, limit: int = 10) -> list[dict]:
        """특정 시리즈의 최근 관측값 가져오기."""
        if not self.is_available():
            return []
        try:
            async with self._make_client() as client:
                params = {
                    "api_key": FRED_API_KEY,
                    "series_id": series_id,
                    "limit": limit,
                    "sort_order": "desc",
                    "file_type": "json",
                }
                resp = await self._get_with_retry(
                    client,
                    f"{FRED_BASE_URL}/series/observations",
                    params=params,
                )
                if resp is None or resp.status_code != 200:
                    return []
                return resp.json().get("observations", [])
        except Exception as e:
            logger.error(f"[fred] 관측값 조회 실패: {e}")
            return []
