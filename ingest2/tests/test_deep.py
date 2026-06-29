"""깊은 분류 검증 — 병합/적용 로직 (Gemini 콜러블 stub, 네트워크 없음)."""
from __future__ import annotations

from datetime import UTC, datetime

from ingest2.classify.deep import DeepClassification, apply, deep_classify, run_deep_classify
from ingest2.schema import NewsItem
from ingest2.store.news_store import NewsStore

NOW = datetime(2026, 6, 22, 12, tzinfo=UTC)


def _item(native="1", tickers_direct=None, indirect=None) -> NewsItem:
    return NewsItem(
        item_id=f"rss_x:{native}",
        source_id="rss_x",
        source_native_id=native,
        trust_tier=3,
        title="Microsoft expands AI datacenter capex",
        url="http://x",
        collected_at=NOW,
        filter_status="passed",
        tickers_direct=tickers_direct or [],
        tickers_indirect=indirect or [],
    )


def _stub(direct=("MSFT",), indirect=("NVDA", "MU"), event="ai_capex", relevant=True):
    def llm(prompt: str) -> DeepClassification:
        return DeepClassification(
            tickers_direct=list(direct),
            tickers_indirect=list(indirect),
            event_type=event,
            us_market_relevant=relevant,
        )
    return llm


def test_apply_adds_indirect_and_event():
    item = _item(tickers_direct=["MSFT"])
    apply(item, deep_classify(item, _stub()))
    assert item.tickers_direct == ["MSFT"]
    assert item.tickers_indirect == ["NVDA", "MU"]
    assert item.event_type == "ai_capex"
    assert "llm_classified" in item.flags


def test_indirect_excludes_direct():
    item = _item(tickers_direct=["MSFT"])
    apply(item, deep_classify(item, _stub(indirect=("MSFT", "NVDA"))))  # MSFT는 직접 → 간접 제외
    assert item.tickers_indirect == ["NVDA"]


def test_invalid_event_ignored():
    item = _item()
    item.event_type = "earnings"
    apply(item, deep_classify(item, _stub(event="not-a-real-event")))
    assert item.event_type == "earnings"  # 통제어휘 밖 → 무시


def test_not_relevant_flag():
    item = _item()
    apply(item, deep_classify(item, _stub(relevant=False)))
    assert "not_us_relevant" in item.flags


def test_run_only_passed_and_missing(tmp_path):
    store = NewsStore(tmp_path / "news.db")
    store.save(_item(native="ok", tickers_direct=["MSFT"]))
    store.save(_item(native="has_indirect", indirect=["AMD"]))   # 이미 간접 → 건너뜀
    rej = _item(native="rej")
    rej.filter_status = "rejected"
    store.save(rej)                                              # 미통과 → 건너뜀
    stats = run_deep_classify(store, _stub(), only_missing_indirect=True)
    assert stats["called"] == 1 and stats["with_indirect"] == 1
    assert store.get("rss_x:ok").tickers_indirect == ["NVDA", "MU"]
    assert store.get("rss_x:rej").tickers_indirect == []
