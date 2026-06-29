"""스키마 검증 테스트."""
from __future__ import annotations

from datetime import datetime, timezone

from src.ingest.schema import Event, RawNews


def _sample(idx: int, ticker: str = "NVDA") -> RawNews:
    return RawNews(
        id=f"id-{idx}",
        title=f"sample title {idx}",
        description="desc",
        published_at=datetime(2026, 5, idx + 1, tzinfo=timezone.utc),
        url=f"https://example.com/{idx}",
        publisher=f"pub-{idx % 2}",
        tickers=[ticker],
    )


def test_raw_news_roundtrip():
    n = _sample(1)
    dumped = n.model_dump(mode="json")
    restored = RawNews(**dumped)
    assert restored == n


def test_event_from_cluster_aggregates():
    items = [_sample(i) for i in range(3)]
    event = Event.from_cluster("c1", items, summary="merged summary")

    assert event.id == "c1"
    assert event.spread == 3
    assert event.summary == "merged summary"
    assert len(event.source_urls) == 3
    assert set(event.publishers) == {"pub-0", "pub-1"}
    assert event.tickers_mentioned == ["NVDA"]
    assert event.occurred_at == items[0].published_at  # earliest
