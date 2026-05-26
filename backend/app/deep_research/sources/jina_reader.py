from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import urlparse

from app.deep_research.config import JINA_BASE_URL, JINA_API_KEY, JINA_RATE_LIMIT_RPM
from app.deep_research.models import ExtractedContent
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

_jina_semaphore = asyncio.Semaphore(5)  # 동시 요청 제한


class JinaReaderSource(BaseSource):
    """Jina Reader — 웹 페이지 전문 추출 (무료, API 키 없이 사용 가능)."""

    source_type = "jina"

    def is_available(self) -> bool:
        return True  # 항상 사용 가능

    async def search(self, query: str, **kwargs) -> list:
        return []  # Jina는 검색이 아닌 추출 전용

    async def extract(self, url: str) -> ExtractedContent | None:
        """단일 URL에서 전문 추출."""
        reader_url = f"{JINA_BASE_URL}{url}"
        headers = {"Accept": "text/plain"}
        if JINA_API_KEY:
            headers["Authorization"] = f"Bearer {JINA_API_KEY}"

        try:
            async with _jina_semaphore:
                async with self._make_client() as client:
                    resp = await self._get_with_retry(client, reader_url, headers=headers)
                    if resp is None or resp.status_code != 200:
                        return None
                    content = resp.text
                    if len(content) < 100:
                        return None

                    domain = urlparse(url).netloc
                    title = _extract_title(content)
                    word_count = len(content.split())

                    return ExtractedContent(
                        url=url,
                        title=title,
                        content=content[:50000],  # 최대 5만자
                        domain=domain,
                        word_count=word_count,
                    )
        except Exception as e:
            logger.error(f"[jina] 추출 실패 {url}: {e}")
            return None

    async def extract_batch(self, urls: list[str], max_concurrent: int = 5) -> list[ExtractedContent]:
        """여러 URL 병렬 추출."""
        sem = asyncio.Semaphore(max_concurrent)
        delay = 60.0 / JINA_RATE_LIMIT_RPM

        async def _safe_extract(url: str) -> ExtractedContent | None:
            async with sem:
                result = await self.extract(url)
                await asyncio.sleep(delay)
                return result

        tasks = [_safe_extract(u) for u in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, ExtractedContent)]


def _extract_title(content: str) -> str:
    lines = content.split("\n")
    for line in lines[:10]:
        line = line.strip()
        if line.startswith("Title:"):
            return line[6:].strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return lines[0].strip()[:200] if lines else "제목 없음"
