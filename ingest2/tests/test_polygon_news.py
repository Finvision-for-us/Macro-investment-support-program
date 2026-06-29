"""D10 검증 — Polygon 뉴스 API 어댑터 정규화 + 수집 윈도우 파라미터."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from ingest2.collect.polygon_news import PolygonNewsCollector
from ingest2.schema import RawRecord


def _raw() -> RawRecord:
    payload = json.dumps(
        {
            "id": "art-1",
            "title": "Nvidia shares rise on AI demand",
            "description": "Data center demand remains strong.",
            "article_url": "https://example.com/nvda",
            "published_utc": "2026-06-22T12:00:00Z",
            "publisher_name": "Example News",
            "author": "A. Reporter",
            "tickers": ["NVDA", "AMD"],
            "keywords": ["AI", "Semiconductors"],
        }
    )
    return RawRecord(
        source_id="polygon_news",
        source_native_id="art-1",
        content_type="json",
        payload=payload,
        url="https://example.com/nvda",
        fetched_at=datetime(2026, 6, 22, 13, tzinfo=UTC),
    )


def test_normalize_builds_newsitem():
    item = PolygonNewsCollector().normalize(_raw())
    assert item.item_id == "polygon_news:art-1"
    assert item.trust_tier == 2
    assert item.title == "Nvidia shares rise on AI demand"
    assert item.summary == "Data center demand remains strong."
    assert item.source_name == "Example News"
    assert item.published_at == datetime(2026, 6, 22, 12, tzinfo=UTC)
    assert item.raw_category == "AI,Semiconductors"
    assert item.tickers_direct == ["NVDA", "AMD"]
    assert item.source_meta == {"api": "polygon"}


def test_fetch_uses_window_and_dedups_native_ids():
    calls = []
    article = SimpleNamespace(
        id="art-1",
        title="title",
        description="summary",
        article_url="https://example.com/a",
        published_utc="2026-06-22T12:00:00Z",
        publisher={"name": "Example"},
        author="",
        tickers=["NVDA"],
        keywords=[],
    )

    class FakeClient:
        def list_ticker_news(self, **kwargs):
            calls.append(kwargs)
            return [article, article]

    collector = PolygonNewsCollector(
        ticker="NVDA",
        limit=25,
        client_factory=lambda: FakeClient(),
    )
    since = datetime(2026, 6, 22, 0, tzinfo=UTC)
    until = datetime(2026, 6, 23, 0, tzinfo=UTC)
    raws = collector.fetch(since, until)

    assert [r.source_native_id for r in raws] == ["art-1"]
    assert calls == [
        {
            "ticker": "NVDA",
            "published_utc_gte": since.isoformat(),
            "published_utc_lt": until.isoformat(),
            "sort": "published_utc",
            "order": "desc",
            "limit": 25,
        }
    ]
