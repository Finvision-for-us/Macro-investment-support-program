"""그라운딩 검색 소스 — Gemini 구글검색 접지(Grounding with Google Search).

배경: Tavily(월 크레딧)·Parallel(크레딧) 소진 시 검색이 전멸해 리서치가
빈사한다(2026-07-19 실측: 73쿼리 → 소스 9개). 유료 티어에는 그라운딩이
월 5,000 프롬프트 무료(초과 $14/1k)로 포함되므로 이를 제3 검색 엔진으로
배선한다 — 구글 검색 인덱스를 직접 쓰는 셈이라 커버리지도 가장 넓다.

동작(라이브 실측 확정):
- generate_content(tools=[google_search]) 1콜 → 모델이 검색어 3~4개를 스스로
  실행(web_search_queries) → grounding_chunks(제목=도메인명, uri=vertexaisearch
  리다이렉트 URL) + grounding_supports(응답 텍스트 조각 ↔ 청크 매핑)
- 리다이렉트 URL은 그대로 두면 domain_of()가 전부 vertexaisearch로 잡혀
  신뢰도 랭킹·중복제거가 망가진다 → 검색 시점에 실제 URL로 해석(HEAD→GET 폴백)
- 토큰 사용량은 llm_client 집계기에 기록(실비용 리포트 포함), 실행된
  검색어 수는 모듈 카운터로 누적 로그(월 5,000 무료분 소진 추적)
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Optional

from app.deep_research.config import GEMINI_API_KEY
from app.deep_research.models import SearchResult

logger = logging.getLogger(__name__)

# 그라운딩 전용 모델 — lite도 그라운딩 지원(실측). 토큰비 최소화.
GROUNDING_MODEL = os.getenv("GROUNDING_MODEL", "gemini-3.1-flash-lite")
_RESOLVE_TIMEOUT = 6.0
_RESOLVE_CONCURRENCY = 8

_search_count_lock = threading.Lock()
_search_count = 0  # 프로세스 수명 동안 실행된 그라운딩 검색어 수(무료분 추적용)


def _add_search_count(n: int) -> int:
    global _search_count
    with _search_count_lock:
        _search_count += n
        return _search_count


def parse_grounding(resp) -> tuple[list[dict], int]:
    """응답 → ([{uri, title, snippet}], 실행된 검색어 수). 순수 함수(테스트용).

    snippet은 grounding_supports가 해당 청크에 매핑한 응답 텍스트 조각들.
    """
    try:
        gm = resp.candidates[0].grounding_metadata
    except (AttributeError, IndexError, TypeError):
        return [], 0
    if gm is None:
        return [], 0

    chunks = list(getattr(gm, "grounding_chunks", None) or [])
    supports = list(getattr(gm, "grounding_supports", None) or [])
    n_queries = len(getattr(gm, "web_search_queries", None) or [])

    snippets: dict[int, list[str]] = {}
    for sup in supports:
        seg = getattr(sup, "segment", None)
        text = (getattr(seg, "text", "") or "").strip() if seg else ""
        if not text:
            continue
        for idx in (getattr(sup, "grounding_chunk_indices", None) or []):
            snippets.setdefault(idx, []).append(text)

    out: list[dict] = []
    for i, ch in enumerate(chunks):
        web = getattr(ch, "web", None)
        if web is None:
            continue
        uri = getattr(web, "uri", "") or ""
        title = getattr(web, "title", "") or ""
        if not uri:
            continue
        out.append({
            "uri": uri,
            "title": title,
            "snippet": " ".join(snippets.get(i, []))[:600],
        })
    return out, n_queries


async def _resolve_redirects(uris: list[str]) -> dict[str, str]:
    """vertexaisearch 리다이렉트 → 실제 URL. 실패는 원본 유지."""
    import httpx
    sem = asyncio.Semaphore(_RESOLVE_CONCURRENCY)
    resolved: dict[str, str] = {}

    async def _one(u: str):
        async with sem:
            try:
                async with httpx.AsyncClient(
                        timeout=_RESOLVE_TIMEOUT, follow_redirects=True) as client:
                    r = await client.head(u)
                    if r.status_code >= 400:  # HEAD 미지원 서버
                        r = await client.get(u)
                    resolved[u] = str(r.url)
            except Exception:
                resolved[u] = u  # 해석 실패 — 리다이렉트 URL 유지(추출은 가능)

    await asyncio.gather(*(_one(u) for u in uris), return_exceptions=True)
    return resolved


class GroundingSearchSource:
    """Gemini 그라운딩을 검색 엔진 인터페이스(search/is_available)로 노출."""

    source_type = "grounding"

    def is_available(self) -> bool:
        return bool(GEMINI_API_KEY)

    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        if not self.is_available():
            return []
        try:
            from google import genai
            from google.genai import types
            from app.deep_research import llm_client

            def _call():
                client = llm_client._get_client()
                return client.models.generate_content(
                    model=GROUNDING_MODEL,
                    contents=query,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        http_options=types.HttpOptions(timeout=45_000),
                    ),
                )

            resp = await asyncio.to_thread(_call)
            llm_client._record_usage(GROUNDING_MODEL, resp)  # 토큰비 실비용 집계
            rows, n_queries = parse_grounding(resp)
            if n_queries:
                total = _add_search_count(n_queries)
                logger.info(f"[grounding] 검색어 {n_queries}개 실행 (누적 {total}) → 청크 {len(rows)}개")
            if not rows:
                return []

            rows = rows[:max_results]
            resolved = await _resolve_redirects([r["uri"] for r in rows])
            results: list[SearchResult] = []
            for r in rows:
                url = resolved.get(r["uri"], r["uri"])
                results.append(SearchResult(
                    url=url,
                    title=r["title"] or url,
                    content=r["snippet"] or f"구글 검색 접지 결과: {r['title']}",
                    source_type="web",
                    relevance_score=0.6,
                ))
            return results
        except Exception as e:
            logger.warning(f"[grounding] 검색 실패({query[:40]!r}): {str(e)[:120]}")
            return []


grounding_source = GroundingSearchSource()
