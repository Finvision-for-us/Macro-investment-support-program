"""접근 가능본 리졸버 (Discovery 엔진의 한 축).

딥리서치의 검색력 중 하나 — 상용 AI가 '구독 없이 읽히는 URL'을 주는 것은
페이월을 뚫는 게 아니라 **같은 정보의 접근 가능한 다른 인스턴스를 찾아내는 것**이다.
이 모듈은 게이트/죽은 URL을 접근 가능한 인스턴스로 해석한다.

합법 범위만 사용한다:
- 공개 웹 아카이브(Internet Archive Wayback)의 이미 크롤된 스냅샷 조회
- (상위 계층) 재전재본·1차자료 검색으로의 위임
인증 우회, 활성 페이월 무력화, ToS 위반 스크래핑은 하지 않는다.

전략은 순서대로 시도한다(체인) — 단일 트릭은 신뢰도가 낮기 때문(실측 확인).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

WAYBACK_AVAILABLE_API = "https://archive.org/wayback/available"

# 하드 페이월/게이트 도메인 — 원본 직접보다 접근 가능한 인스턴스를 우선 탐색.
GATED_DOMAINS = frozenset({
    "wsj.com", "ft.com", "bloomberg.com", "economist.com", "barrons.com",
    "nytimes.com", "seekingalpha.com", "morningstar.com", "spglobal.com",
    "stockzoa.com", "whalewisdom.com", "hedgefollow.com", "gurufocus.com",
})


@dataclass
class AccessibleResult:
    """접근 가능한 인스턴스 해석 결과."""
    original_url: str
    accessible_url: str
    method: str            # "wayback" 등 해석 전략
    timestamp: str = ""    # 스냅샷 시점(YYYYMMDDhhmmss, 있으면)


def is_gated(url: str) -> bool:
    """알려진 페이월/게이트 도메인인지 (휴리스틱)."""
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return any(host == d or host.endswith("." + d) for d in GATED_DOMAINS)


class AccessibleResolver(BaseSource):
    """게이트/죽은 URL을 접근 가능한 인스턴스로 해석하는 다전략 리졸버."""

    source_type = "accessible_resolver"

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, **kwargs):
        return []  # 검색이 아닌 해석 전용

    async def find_accessible_url(self, url: str) -> AccessibleResult | None:
        """접근 가능한 인스턴스를 찾아 반환. 없으면 None.

        전략 순서:
          1) Wayback 최근 스냅샷 (아카이브된 경우)
          (이후 슬라이스에서 재전재본/1차자료 검색 전략을 추가)
        """
        snap = await self._wayback_snapshot(url)
        if snap:
            logger.info(f"[resolver] wayback 해석 성공: {url} → {snap.timestamp}")
            return snap
        return None

    async def _wayback_snapshot(self, url: str) -> AccessibleResult | None:
        """Internet Archive Wayback의 가장 가까운 200 스냅샷을 조회.

        주의: Wayback available API는 url 값을 퍼센트 인코딩하면 빈 결과를 준다(실측).
        따라서 params 딕셔너리 대신 raw로 붙인 엔드포인트를 사용한다.
        """
        try:
            async with self._make_client() as client:
                endpoint = f"{WAYBACK_AVAILABLE_API}?url={url}"
                resp = await self._get_with_retry(client, endpoint)
                if resp is None or resp.status_code != 200:
                    return None
                closest = (resp.json().get("archived_snapshots") or {}).get("closest") or {}
                if closest.get("available") and str(closest.get("status")) == "200" and closest.get("url"):
                    return AccessibleResult(
                        original_url=url,
                        accessible_url=closest["url"],
                        method="wayback",
                        timestamp=closest.get("timestamp", ""),
                    )
        except Exception as e:
            logger.warning(f"[resolver] Wayback 조회 실패 {url}: {e}")
        return None


# 싱글턴 (다른 소스들과 동일 패턴)
accessible_resolver = AccessibleResolver()
