"""P3 검증 — 1차 필터 분류 규칙(신뢰도 등급 인지) + store 반영."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ingest2.filter.basic import classify, run_filter
from ingest2.schema import NewsItem
from ingest2.store.news_store import NewsStore

NOW = datetime(2026, 6, 22, 12, tzinfo=UTC)


def _item(tier=3, title="A normal market headline about Fed", summary="s",
          published=NOW, category="", native="1", source="rss_x") -> NewsItem:
    return NewsItem(
        item_id=f"{source}:{native}",
        source_id=source,
        source_native_id=native,
        trust_tier=tier,
        title=title,
        summary=summary,
        url="http://x",
        raw_category=category,
        published_at=published,
        collected_at=NOW,
    )


def test_too_old_rejected_all_tiers():
    old = _item(published=NOW - timedelta(hours=30))
    assert classify(old, now=NOW, cutoff_hours=24).reasons == ["too_old"]


def test_fresh_passes():
    res = classify(_item(published=NOW - timedelta(hours=1)), now=NOW)
    assert res.status == "passed" and res.reasons == []


def test_no_timestamp_passes_with_flag():
    res = classify(_item(published=None), now=NOW)
    assert res.status == "passed"
    assert res.flags == ["no_timestamp"]


def test_tier1_sec_not_rejected_for_short_or_category():
    # SEC: 제목 짧고 summary 없음 + 카테고리가 폼타입 → tier-1은 면제
    sec = _item(tier=1, title="8-K", summary="", category="8-K", source="sec_edgar")
    assert classify(sec, now=NOW).status == "passed"


def test_short_headline_kept_but_empty_rejected():
    # 짧은 헤드라인은 살림 (짧음 ≠ 쓸모없음)
    assert classify(_item(tier=3, title="Fed cuts", summary=""), now=NOW).status == "passed"
    # 제목·요약 모두 비면 탈락(empty) — 모든 tier
    assert "empty" in classify(_item(tier=3, title="", summary=""), now=NOW).reasons
    assert "empty" in classify(_item(tier=1, title="  ", summary=""), now=NOW).reasons


def test_off_topic_category_rejected_but_no_false_positive():
    assert "off_topic_category" in classify(
        _item(category="Lifestyle"), now=NOW).reasons
    # "transport"가 "sport"로 오탐되면 안 됨
    res = classify(_item(title="Transport stocks rally on freight demand"), now=NOW)
    assert "off_topic_category" not in res.reasons


def test_spam_like_rejected():
    spam = _item(title="Buy now: this hot stock guaranteed to soar")
    assert "spam_like" in classify(spam, now=NOW).reasons


def test_run_filter_updates_store(tmp_path):
    store = NewsStore(tmp_path / "news.db")
    store.save(_item(native="fresh", published=NOW - timedelta(hours=1)))
    store.save(_item(native="old", published=NOW - timedelta(hours=30)))
    stats = run_filter(store, cutoff_hours=24, now=NOW)
    assert stats["passed"] == 1 and stats["rejected"] == 1
    assert store.get("rss_x:old").filter_status == "rejected"
    assert store.get("rss_x:old").rejected_reasons == ["too_old"]
    assert store.get("rss_x:fresh").filter_status == "passed"
