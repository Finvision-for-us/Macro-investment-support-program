from __future__ import annotations
import logging

from app.deep_research.config import TAVILY_API_KEYS
from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# 현재 사용 중인 키 인덱스 (프로세스 내 공유)
_current_key_idx = 0
# 키 인덱스 → 소진 처리 시각. 영구 은퇴가 아니라 쿨다운 후 재시도한다.
# (429가 분당 제한이어도 영구 퇴출되면 몇 분 만에 전 키가 '소진' 처리되고
#  이후 모든 Tavily 검색이 프로세스 재시작까지 빈 결과가 되는 버그 방지.)
_exhausted_at: dict[int, float] = {}
_KEY_COOLDOWN_SECONDS = 15 * 60  # 15분 후 재시도


def _is_exhausted(idx: int) -> bool:
    import time as _time
    ts = _exhausted_at.get(idx)
    if ts is None:
        return False
    if _time.time() - ts >= _KEY_COOLDOWN_SECONDS:
        del _exhausted_at[idx]  # 쿨다운 경과 → 복귀
        logger.info(f"[tavily] 키 #{idx} 쿨다운 종료 → 재사용")
        return False
    return True


def _get_active_key() -> str | None:
    global _current_key_idx
    if not TAVILY_API_KEYS:
        return None
    # 소진되지 않은(또는 쿨다운이 끝난) 키 찾기
    for _ in range(len(TAVILY_API_KEYS)):
        if not _is_exhausted(_current_key_idx):
            return TAVILY_API_KEYS[_current_key_idx]
        _current_key_idx = (_current_key_idx + 1) % len(TAVILY_API_KEYS)
    return None  # 모든 키가 쿨다운 중


def _mark_exhausted_and_rotate():
    global _current_key_idx
    import time as _time
    idx = _current_key_idx
    _exhausted_at[idx] = _time.time()
    _current_key_idx = (_current_key_idx + 1) % len(TAVILY_API_KEYS)
    remaining = sum(1 for i in range(len(TAVILY_API_KEYS)) if i not in _exhausted_at)
    if remaining > 0:
        logger.warning(f"[tavily] 키 #{idx} 소진(쿨다운 {_KEY_COOLDOWN_SECONDS//60}분) → 다음 키로 전환 (잔여 {remaining}개)")
    else:
        logger.error(f"[tavily] 모든 Tavily 키 쿨다운 중 — {_KEY_COOLDOWN_SECONDS//60}분 내 자동 복귀")


class TavilySearchSource(BaseSource):
    """Tavily 검색 API — 다중 키 자동 로테이션 지원."""

    source_type = "tavily"

    def is_available(self) -> bool:
        return bool(_get_active_key())

    async def search(self, query: str, search_depth: str = "basic", max_results: int = 10, num_results: int | None = None, **kwargs) -> list[SearchResult]:
        # num_results는 하위호환 alias (파라미터명 불일치로 조용히 무시되던 버그 방지)
        if num_results is not None:
            max_results = num_results
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
