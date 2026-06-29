"""M3 Day 1~2 novelty 단위 테스트 (LLM 없음)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from src.score.novelty import (
    NOVELTY_SIM_THRESHOLD,
    compute_novelty,
)
from src.ingest.schema import Event


def _ev(idx: int) -> Event:
    return Event(
        id=f"e{idx}",
        title=f"Event {idx}",
        summary="s",
        occurred_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        source_urls=[f"http://x.com/{idx}"],
        publishers=["p"],
        tickers_mentioned=["NVDA"],
        spread=2,
    )


def test_novelty_zero_history_all_one():
    events = [_ev(1), _ev(2)]
    embs = np.eye(2, dtype=np.float32)
    scores = compute_novelty(events, embs, historical_events=[], historical_embeddings=None)
    assert scores == {"e1": 1.0, "e2": 1.0}


def test_novelty_high_when_no_similar():
    events = [_ev(1)]
    current_emb = np.array([[1.0, 0.0]], dtype=np.float32)
    historical = [_ev(2)]
    hist_emb = np.array([[0.0, 1.0]], dtype=np.float32)  # 직교 → 유사도 0
    scores = compute_novelty(events, current_emb, historical, hist_emb)
    assert scores["e1"] == 1.0  # similar count = 0


def test_novelty_decreases_with_more_similar():
    events = [_ev(1)]
    current_emb = np.array([[1.0, 0.0]], dtype=np.float32)
    # 5개의 매우 유사한 historical
    historical = [_ev(i + 10) for i in range(5)]
    hist_emb = np.array([[1.0, 0.0]] * 5, dtype=np.float32)
    scores = compute_novelty(events, current_emb, historical, hist_emb)
    # count = 5 → novelty = 1 / (1 + log(6)) ≈ 0.36
    assert 0.2 < scores["e1"] < 0.5


def test_novelty_threshold_filters_dissimilar():
    events = [_ev(1)]
    current_emb = np.array([[1.0, 0.0]], dtype=np.float32)
    # historical 5개 중 1개만 유사 (cos=0.99), 나머지는 직교
    historical = [_ev(i + 10) for i in range(5)]
    hist_emb = np.array(
        [[0.99, 0.1], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0]],
        dtype=np.float32,
    )
    scores = compute_novelty(events, current_emb, historical, hist_emb, sim_threshold=0.7)
    # count = 1 → novelty = 1 / (1 + log(2)) ≈ 0.59
    assert 0.5 < scores["e1"] < 0.7
