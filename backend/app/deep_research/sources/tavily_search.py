from __future__ import annotations
import logging

from app.deep_research.config import TAVILY_API_KEYS
from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# 현재 사용 중인 키 인덱스 (프로세스 내 공유)
_current_key_idx = 0
_exhausted_keys: set[int] = set()


def _get_active_key() -> str | None:
    global _current_key_idx
    if not TAVILY_API_KEYS:
        return None
    # 소진되지 않은 키 찾기
    for _ in range(len(TAVILY_API_KEYS)):
        if _current_key_idx not in _exhausted_keys:
            return TAVILY_API_KEYS[_current_key_idx]
        _current_key_idx = (_current_key_idx + 1) % len(TAVILY_API_KEYS)
    return None  # 모든 키 소진


def _mark_exhausted_and_rotate():
    global _current_key_idx
    idx = _current_key_idx
    _exhausted_keys.add(idx)
    _current_key_idx = (_current_key_idx + 1) % len(TAVILY_API_KEYS)
    remaining = len(TAVILY_API_KEYS) - len(_exhausted_keys)
    if remaining > 0:
        logger.warning(f"[tavily] 키 #{idx} 소진 → 다음 키로 전환 (잔여 {remaining}개)")
    else:
        logger.error("[tavily] 모든 Tavily 키 소진")


class TavilySearchSource(BaseSource):
    """Tavily 검색 API — 다중 키 자동 로테이션 지원."""

    source_type = "tavily"

    def is_available(self) -> bool:
        return bool(_get_active_key())

    async def search(self, query: str, search_depth: str = "basic", max_results: int = 10, **kwargs) -> list[SearchResult]:
        if not self.is_available():
            logger.warning("[tavily] 사용 가능한 키 없음 — 건너뜀")
            return []

        # 키 소진 시 자동 재시도 (최대 키 개수만큼)
        for attempt in range(len(TAVILY_API_KEYS)):
            api_key = _get_active_key()
            if not api_key:
                break
            try:
                async with self._make_client() as client:
                    resp = await self._post_with_retry(
                        client,
                        TAVILY_SEARCH_URL,
                        json={
                            "api_key": api_key,
                            "query": query,
                            "search_depth": search_depth,
                            "max_results": max_results,
                            "include_answer": False,
                            "include_raw_content": False,
                        },
                        headers={"Content-Type": "application/json"},
                    )
                    if resp is None:
                        return []

                    # 429: 한도 초과 → 다음 키로
                    if resp.status_code in (429, 402):
                        _mark_exhausted_and_rotate()
                        continue

                    if resp.status_code != 200:
                        logger.warning(f"[tavily] 검색 실패: {resp.status_code}")
                        return []

                    results = []
                    for item in resp.json().get("results", []):
                        results.append(SearchResult(
                            url=item.get("url", ""),
                            title=item.get("title", ""),
                            content=item.get("content", ""),
                            source_type=self.source_type,
                            relevance_score=item.get("score", 0.0),
                            published_date=item.get("published_date"),
                        ))
                    logger.info(f"[tavily] '{query[:50]}' → {len(results)}건")
                    return results

            except Exception as e:
                logger.error(f"[tavily] 예외: {e}")
                return []

        logger.warning("[tavily] 모든 키 소진 또는 실패")
        return []
