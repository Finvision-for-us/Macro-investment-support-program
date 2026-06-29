"""P(a) 검증 — 경량 분류: CIK→티커, 보수적 텍스트매칭, 이벤트 키워드 (오프라인)."""
from __future__ import annotations

from datetime import UTC, datetime

from ingest2.classify.basic import classify, run_classify
from ingest2.classify.events import event_for_text
from ingest2.classify.tickers import TickerMap
from ingest2.schema import NewsItem
from ingest2.store.news_store import NewsStore

ROWS = [
    {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    {"cik_str": 723125, "ticker": "MU", "title": "Micron Technology, Inc."},
    {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    {"cik_str": 794367, "ticker": "M", "title": "Macy's, Inc."},
]
TM = TickerMap.from_rows(ROWS)
NOW = datetime(2026, 6, 22, 12, tzinfo=UTC)


def _item(source="rss_x", title="t", summary="", native="1", cik="") -> NewsItem:
    return NewsItem(
        item_id=f"{source}:{native}",
        source_id=source,
        source_native_id=native,
        trust_tier=1 if source == "sec_edgar" else 3,
        title=title,
        summary=summary,
        url="http://x",
        collected_at=NOW,
        filter_status="passed",
        source_meta={"cik": cik} if cik else {},
    )


def test_sec_cik_to_ticker():
    sec = _item(source="sec_edgar", title="8-K - NVIDIA Corp", native="a", cik="0001045810")
    res = classify(sec, TM)
    assert res.tickers_direct == ["NVDA"]


def test_rss_alias_and_event():
    res = classify(_item(title="Micron earnings are a must-watch market event"), TM)
    assert res.tickers_direct == ["MU"]
    assert res.event_type == "earnings"


def test_rss_dollar_symbol():
    res = classify(_item(title="Why (NVDA) keeps surging on AI demand"), TM)
    assert res.tickers_direct == ["NVDA"]


def test_parenthesis_symbol_requires_complete_symbol():
    res = classify(_item(title="MeeC(Most Energy Efficient Core) wins partner award"), TM)
    assert res.tickers_direct == []


def test_existing_api_tickers_are_preserved():
    item = _item(source="polygon_news", title="Semiconductor demand rises")
    item.tickers_direct = ["NVDA"]
    res = classify(item, TM)
    assert res.tickers_direct == ["NVDA"]


def test_single_letter_api_ticker_requires_confirmation():
    item = _item(
        source="polygon_news",
        title="Mavenir wins partner award for MeeC(Most Energy Efficient Core)",
    )
    item.tickers_direct = ["M"]
    res = classify(item, TM)
    assert res.tickers_direct == []


def test_single_letter_api_ticker_kept_when_company_confirmed():
    item = _item(source="polygon_news", title="Macy's reports quarterly earnings")
    item.tickers_direct = ["M"]
    res = classify(item, TM)
    assert res.tickers_direct == ["M"]


def test_no_false_positive_common_word():
    # "Federal Reserve"의 'reserve'가 잡주(RSRV)로 오탐되면 안 됨 (고정밀 별칭만 매칭)
    res = classify(_item(title="Alan Greenspan, former Fed chairman, dies"), TM)
    assert res.tickers_direct == []


def test_event_keyword_mapping():
    assert event_for_text("Company A to acquire Company B") == "m_and_a"
    assert event_for_text("Firm files for IPO next month") == "ipo"
    assert event_for_text("Just a normal headline") is None


def test_run_classify_only_passed(tmp_path):
    store = NewsStore(tmp_path / "news.db")
    store.save(_item(title="Micron earnings beat", native="ok"))
    rej = _item(title="Micron earnings beat", native="rej")
    rej.filter_status = "rejected"
    store.save(rej)
    stats = run_classify(store, TM)
    assert stats["classified"] == 1 and stats["with_ticker"] == 1
    assert store.get("rss_x:ok").tickers_direct == ["MU"]
    assert store.get("rss_x:rej").tickers_direct == []  # rejected는 분류 안 함
