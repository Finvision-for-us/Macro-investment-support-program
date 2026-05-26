from __future__ import annotations
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivSource(BaseSource):
    """arXiv — 학술 논문 검색 (무료, API 키 불필요)."""

    source_type = "arxiv"

    def is_available(self) -> bool:
        return True  # 항상 사용 가능

    async def search(self, query: str, max_results: int = 5, **kwargs) -> list[SearchResult]:
        try:
            async with self._make_client() as client:
                params = {
                    "search_query": f"all:{quote_plus(query)}",
                    "start": 0,
                    "max_results": max_results,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                }
                resp = await self._get_with_retry(client, ARXIV_API_URL, params=params)
                if resp is None or resp.status_code != 200:
                    return []

                root = ET.fromstring(resp.text)
                results = []
                for entry in root.findall("atom:entry", NS):
                    arxiv_id = entry.findtext("atom:id", "", NS) or ""
                    title = entry.findtext("atom:title", "", NS).strip().replace("\n", " ")
                    summary = entry.findtext("atom:summary", "", NS).strip().replace("\n", " ")
                    published = entry.findtext("atom:published", "", NS)[:10]
                    authors = [
                        a.findtext("atom:name", "", NS)
                        for a in entry.findall("atom:author", NS)
                    ][:3]

                    results.append(SearchResult(
                        url=arxiv_id,
                        title=title,
                        content=f"{', '.join(authors)} ({published})\n{summary[:500]}",
                        source_type=self.source_type,
                        relevance_score=0.6,
                        published_date=published,
                    ))
                logger.info(f"[arxiv] '{query[:50]}' → {len(results)}건")
                return results
        except ET.ParseError as e:
            logger.error(f"[arxiv] XML 파싱 오류: {e}")
            return []
        except Exception as e:
            logger.error(f"[arxiv] 예외: {e}")
            return []
