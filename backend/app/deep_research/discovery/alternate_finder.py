"""대체 인스턴스 검색 (Discovery 엔진의 핵심 축).

딥리서치 검색력의 본질 — 하나의 정보는 웹 여러 곳에 존재한다. 특정 출처가 막히거나
부족하면 **같은 정보를 담은 다른 호스트의 접근 가능한 인스턴스**(재전재본·1차자료·요약본
·독립 보도 등)를 능동적으로 검색해 찾아낸다.

특정 사이트나 페이월에 종속되지 않는 '일반 능력'이다 — 어떤 주제/URL에도 동일하게 작동한다.
합법 범위: 공개 검색으로 접근 가능한 다른 출처를 찾는 것. 인증 우회는 하지 않는다.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from app.deep_research.models import SearchResult

logger = logging.getLogger(__name__)


def _host(url: str) -> str:
    """URL → 호스트(www. 제거, 소문자)."""
    h = urlparse(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


class AlternateInstanceFinder:
    """주제/URL에 대해 '다른 호스트의 접근 가능한 인스턴스'를 검색으로 발견한다."""

    def __init__(self):
        self._sources: list = []  # 검색 가능한 소스(tavily/parallel 등)

    def set_sources(self, *sources) -> None:
        """검색 소스 주입. None은 무시(파이프라인이 가용 소스를 넘겨줌)."""
        self._sources = [s for s in sources if s is not None]

    async def find_alternates(
        self,
        topic: str,
        exclude_url: str | None = None,
        exclude_hosts: set[str] | None = None,
        max_per_source: int = 8,
        limit: int = 8,
    ) -> list[SearchResult]:
        """topic(제목/핵심어/주장)에 대한 대체 인스턴스 후보를 반환.

        - exclude_url의 호스트 및 exclude_hosts는 제외(원본/이미 본 출처 배제)
        - 호스트 다양성 우선(호스트당 최고 관련도 1건), 관련도 내림차순
        """
        topic = (topic or "").strip()
        if not topic or not self._sources:
            return []

        blocked = set(exclude_hosts or set())
        if exclude_url:
            blocked.add(_host(exclude_url))

        collected: list[SearchResult] = []
        for src in self._sources:
            try:
                if not src.is_available():
                    continue
                res = await src.search(topic, max_results=max_per_source)
                if res:
                    collected.extend(res)
            except Exception as e:
                logger.warning(
                    f"[alternate] 소스 검색 실패({getattr(src, 'source_type', '?')}): {e}"
                )

        # 차단 호스트 제외 + 호스트 다양성(호스트당 최고 관련도 1건)
        best_by_host: dict[str, SearchResult] = {}
        for r in sorted(collected, key=lambda x: x.relevance_score, reverse=True):
            if not r.url:
                continue
            h = _host(r.url)
            if not h or h in blocked or h in best_by_host:
                continue
            best_by_host[h] = r

        alternates = list(best_by_host.values())[:limit]
        logger.info(
            f"[alternate] '{topic[:50]}' → 대체 인스턴스 {len(alternates)}개 "
            f"(고유 호스트 {len(best_by_host)})"
        )
        return alternates


# 싱글턴 (official_source_searcher 등과 동일 패턴 — 파이프라인이 set_sources로 주입)
alternate_instance_finder = AlternateInstanceFinder()
