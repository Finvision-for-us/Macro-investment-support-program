from __future__ import annotations
import logging
from urllib.parse import quote

from app.deep_research.config import DART_API_KEY
from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

DART_BASE_URL = "https://opendart.fss.or.kr/api"


class DartSource(BaseSource):
    """DART (금융감독원) — 한국 기업 공시 검색."""

    source_type = "dart"

    def is_available(self) -> bool:
        return bool(DART_API_KEY)

    async def search(self, query: str, num_results: int = 10, **kwargs) -> list[SearchResult]:
        if not self.is_available():
            logger.warning("[dart] API 키 없음 — 건너뜀")
            return []
        try:
            async with self._make_client() as client:
                params = {
                    "crtfc_key": DART_API_KEY,
                    "corp_name": query,
                    "bgn_de": "20200101",
                    "pblntf_ty": "A",  # 정기공시
                    "page_count": num_results,
                }
                resp = await self._get_with_retry(
                    client,
                    f"{DART_BASE_URL}/list.json",
                    params=params,
                )
                if resp is None or resp.status_code != 200:
                    return []
                data = resp.json()
                if data.get("status") != "000":
                    logger.warning(f"[dart] API 오류: {data.get('message')}")
                    return []

                results = []
                for item in data.get("list", [])[:num_results]:
                    rcept_no = item.get("rcept_no", "")
                    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                    results.append(SearchResult(
                        url=url,
                        title=f"[{item.get('report_nm', '')}] {item.get('corp_name', '')}",
                        content=f"{item.get('corp_name', '')} - {item.get('report_nm', '')} ({item.get('rcept_dt', '')})",
                        source_type=self.source_type,
                        relevance_score=0.7,
                        published_date=item.get("rcept_dt"),
                    ))
                logger.info(f"[dart] '{query[:50]}' → {len(results)}건")
                return results
        except Exception as e:
            logger.error(f"[dart] 예외: {e}")
            return []
