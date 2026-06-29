"""P1 검증 게이트 — 저장→조회→재수집 중복차단."""
from __future__ import annotations

from datetime import UTC, datetime

from ingest2.schema import NewsItem, RawRecord
from ingest2.store.news_store import NewsStore
from ingest2.store.raw_store import RawStore


def _item(native_id: str = "1", source: str = "finnhub") -> NewsItem:
    return NewsItem(
        item_id=f"{source}:{native_id}",
        source_id=source,
        source_native_id=native_id,
        trust_tier=2,
        title="title",
        url="http://example.com/a",
        collected_at=datetime.now(UTC),
    )


def test_news_store_insert_and_get(tmp_path):
    store = NewsStore(tmp_path / "news.db")
    assert store.save(_item()) is True
    assert store.count() == 1
    got = store.get("finnhub:1")
    assert got is not None and got.title == "title"


def test_news_store_dedup_on_reingest(tmp_path):
    store = NewsStore(tmp_path / "news.db")
    assert store.save(_item()) is True      # 최초 삽입
    assert store.save(_item()) is False     # 재수집 동일 item_id → 무시
    assert store.count() == 1


def test_raw_store_appends_jsonl(tmp_path):
    store = RawStore(tmp_path / "raw")
    raw = RawRecord(
        source_id="finnhub",
        source_native_id="1",
        content_type="json",
        payload='{"a": 1}',
        fetched_at=datetime(2026, 6, 22, tzinfo=UTC),
    )
    p1 = store.save(raw)
    p2 = store.save(raw)
    assert p1 == p2                         # 같은 소스+날짜 → 같은 파일
    assert p1.read_text(encoding="utf-8").count("\n") == 2  # 덮어쓰지 않고 append
