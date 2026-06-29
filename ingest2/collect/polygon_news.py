"""Polygon.io 뉴스 API 수집 어댑터 (trust_tier=2)."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from polygon import RESTClient

from src.config import POLYGON_API_KEY

from ..schema import NewsItem, RawRecord
from .base import BaseCollector


class MissingPolygonKeyError(RuntimeError):
    """Polygon API 키 미설정."""


def _client() -> RESTClient:
    if not POLYGON_API_KEY:
        raise MissingPolygonKeyError("POLYGON_API_KEY not set. Add it to .env.")
    return RESTClient(POLYGON_API_KEY)


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _article_to_dict(article: Any) -> dict:
    publisher = _get(article, "publisher") or {}
    return {
        "id": _get(article, "id", ""),
        "title": _get(article, "title", "") or "",
        "description": _get(article, "description", "") or "",
        "article_url": _get(article, "article_url", "") or _get(article, "url", "") or "",
        "published_utc": (
            _get(article, "published_utc").isoformat()
            if isinstance(_get(article, "published_utc"), datetime)
            else _get(article, "published_utc")
        ),
        "publisher_name": _get(publisher, "name", "") or "",
        "author": _get(article, "author", "") or "",
        "tickers": list(_get(article, "tickers", None) or []),
        "keywords": list(_get(article, "keywords", None) or []),
    }


class PolygonNewsCollector(BaseCollector):
    """Polygon market news collector.

    ``ticker=None`` requests Polygon's broad ticker-news feed. Supplying a ticker narrows
    collection while preserving the same ``source_id`` so duplicate articles collapse.
    """

    source_id = "polygon_news"
    trust_tier = 2

    def __init__(
        self,
        ticker: str | None = None,
        limit: int = 100,
        client_factory: Callable[[], RESTClient] = _client,
    ) -> None:
        self.ticker = ticker
        self.limit = limit
        self.client_factory = client_factory

    def fetch(self, since: datetime, until: datetime) -> list[RawRecord]:
        now = datetime.now(UTC)
        client = self.client_factory()
        out: list[RawRecord] = []
        seen: set[str] = set()
        for article in client.list_ticker_news(
            ticker=self.ticker,
            published_utc_gte=since.isoformat(),
            published_utc_lt=until.isoformat(),
            sort="published_utc",
            order="desc",
            limit=self.limit,
        ):
            d = _article_to_dict(article)
            native_id = d["id"] or d["article_url"]
            if not native_id or native_id in seen:
                continue
            seen.add(native_id)
            out.append(
                RawRecord(
                    source_id=self.source_id,
                    source_native_id=native_id,
                    content_type="json",
                    payload=json.dumps(d, ensure_ascii=False),
                    url=d["article_url"] or None,
                    fetched_at=now,
                )
            )
        return out

    def normalize(self, raw: RawRecord) -> NewsItem:
        d = json.loads(raw.payload)
        published = _parse_dt(d.get("published_utc"))
        return NewsItem(
            item_id=self.make_item_id(raw.source_id, raw.source_native_id),
            source_id=raw.source_id,
            source_native_id=raw.source_native_id,
            trust_tier=self.trust_tier,
            title=d.get("title", ""),
            summary=d.get("description", ""),
            url=d.get("article_url") or (raw.url or ""),
            canonical_url=d.get("article_url") or raw.url,
            source_name=d.get("publisher_name", "") or "Polygon",
            author=d.get("author", ""),
            published_at=published,
            collected_at=raw.fetched_at,
            language="en",
            raw_category=",".join(d.get("keywords", [])),
            tickers_direct=list(d.get("tickers", [])),
            source_meta={"api": "polygon"},
        )
