from __future__ import annotations
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.deep_research.config import (
    HTTP_TIMEOUT, HTTP_CONNECT_TIMEOUT, MAX_RETRIES, RETRY_BASE_DELAY
)
from app.deep_research.models import SearchResult

logger = logging.getLogger(__name__)


class BaseSource(ABC):
    """모든 데이터 소스의 추상 기반 클래스."""

    source_type: str = "unknown"

    @abstractmethod
    def is_available(self) -> bool:
        """API 키 등 필수 설정이 있는지 확인."""
        ...

    @abstractmethod
    async def search(self, query: str, **kwargs) -> list[SearchResult]:
        """검색 실행. 실패 시 빈 리스트 반환 (예외 전파 금지)."""
        ...

    # ── 공통 HTTP 유틸 ──

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
            follow_redirects=True,
        )

    async def _get_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        max_retries: int = MAX_RETRIES,
    ) -> Optional[httpx.Response]:
        delay = RETRY_BASE_DELAY
        for attempt in range(max_retries):
            try:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", delay * 2))
                    logger.warning(f"[{self.source_type}] Rate limit, waiting {wait}s")
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                if resp.status_code >= 500:
                    logger.warning(f"[{self.source_type}] Server error {resp.status_code}, retry {attempt+1}")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning(f"[{self.source_type}] Network error: {e}, retry {attempt+1}")
                await asyncio.sleep(delay)
                delay *= 2
        logger.error(f"[{self.source_type}] 최대 재시도 초과: {url}")
        return None

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        json: Optional[dict] = None,
        headers: Optional[dict] = None,
        max_retries: int = MAX_RETRIES,
    ) -> Optional[httpx.Response]:
        delay = RETRY_BASE_DELAY
        for attempt in range(max_retries):
            try:
                resp = await client.post(url, json=json, headers=headers)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", delay * 2))
                    logger.warning(f"[{self.source_type}] Rate limit, waiting {wait}s")
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                if resp.status_code >= 500:
                    logger.warning(f"[{self.source_type}] Server error {resp.status_code}, retry {attempt+1}")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning(f"[{self.source_type}] Network error: {e}, retry {attempt+1}")
                await asyncio.sleep(delay)
                delay *= 2
        logger.error(f"[{self.source_type}] 최대 재시도 초과: {url}")
        return None
