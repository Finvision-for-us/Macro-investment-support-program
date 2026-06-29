"""M2 causal edges 단위 테스트 (LLM 호출 없이)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from src.causal.edges import (
    EMBEDDING_SIM_THRESHOLD,
    TIME_WINDOW_DAYS,
    candidate_pairs,
    merge_edges,
)
from src.causal.schema import CausalEdge
from src.ingest.schema import Event


def _ev(idx: int, tickers: list[str], days_offset: int = 0) -> Event:
    return Event(
        id=f"e{idx}",
        title=f"Event {idx}",
        summary="s",
        occurred_at=datetime(2026, 5, 10, tzinfo=timezone.utc)
        + timedelta(days=days_offset),
        source_urls=[f"http://x.com/{idx}"],
        publishers=["p"],
        tickers_mentioned=tickers,
        spread=2,
    )


def test_candidate_pairs_passes_on_ticker_overlap():
    a = _ev(0, ["NVDA"], days_offset=0)
    b = _ev(1, ["NVDA"], days_offset=100)  # 시간 멀어도
    embs = np.eye(2, dtype=np.float32)  # 유사도 0이어도
    pairs = candidate_pairs([a, b], embs)
    assert len(pairs) == 1
    assert pairs[0]["shared_tickers"] == ["NVDA"]


def test_candidate_pairs_passes_on_time_proximity():
    a = _ev(0, ["NVDA"], days_offset=0)
    b = _ev(1, ["AAPL"], days_offset=3)  # 티커 다르지만 가까움
    embs = np.eye(2, dtype=np.float32)
    pairs = candidate_pairs([a, b], embs)
    assert len(pairs) == 1
    assert pairs[0]["time_close"] is True


def test_candidate_pairs_skip_when_all_filters_fail():
    a = _ev(0, ["NVDA"], days_offset=0)
    b = _ev(1, ["AAPL"], days_offset=100)  # 티커 다르고 시간 멀고
    embs = np.eye(2, dtype=np.float32)  # 유사도 0
    pairs = candidate_pairs([a, b], embs)
    assert pairs == []


def test_candidate_pairs_passes_on_embedding_sim():
    a = _ev(0, ["NVDA"], days_offset=0)
    b = _ev(1, ["AAPL"], days_offset=100)
    # 거의 같은 방향 → 유사도 매우 높음
    embs = np.array([[1.0, 0.01], [0.99, 0.0]], dtype=np.float32)
    pairs = candidate_pairs([a, b], embs)
    assert len(pairs) == 1
    assert pairs[0]["sim"] >= EMBEDDING_SIM_THRESHOLD


def test_merge_edges_dedupes_by_confidence():
    edges = [
        CausalEdge(
            from_event_id="A",
            to_event_id="B",
            confidence=0.6,
            direction="positive",
            mechanism="m1",
            inferred_by="pairwise_llm",
        ),
        CausalEdge(
            from_event_id="A",
            to_event_id="B",
            confidence=0.8,
            direction="positive",
            mechanism="m2",
            inferred_by="deep_research_claim",
        ),
        CausalEdge(
            from_event_id="A",
            to_event_id="C",
            confidence=0.7,
            direction="negative",
            mechanism="m3",
            inferred_by="pairwise_llm",
        ),
    ]
    merged = merge_edges(edges)
    assert len(merged) == 2
    ab = next(e for e in merged if e.to_event_id == "B")
    assert ab.confidence == 0.8
    assert ab.mechanism == "m2"
