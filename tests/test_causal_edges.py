"""M2 causal edges 단위 테스트 (LLM 호출 없이)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from src.causal.edges import (
    EMBEDDING_SIM_THRESHOLD,
    _build_pair_batch_prompt,
    candidate_pairs,
    infer_pairwise,
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


def test_candidate_pairs_rejects_time_proximity_alone():
    # 설계 결정(edges.py docstring): 시간 근접성 단독은 후보가 아니다.
    # 수집 창(12~48h)에서 모든 쌍이 통과해 LLM 호출이 폭발하므로,
    # 인과 후보는 티커 공유 또는 의미 유사도를 요구한다.
    a = _ev(0, ["NVDA"], days_offset=0)
    b = _ev(1, ["AAPL"], days_offset=3)  # 티커 다름, 시간만 가까움, 유사도 0
    embs = np.eye(2, dtype=np.float32)
    pairs = candidate_pairs([a, b], embs)
    assert pairs == []


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


# ---- infer_pairwise 배치 (LLM 주입) ----

def _three_shared_ticker_events():
    """전부 NVDA 공유 → candidate_pairs가 3쌍 반환. 임베딩은 직교(유사도 0)."""
    evs = [_ev(i, ["NVDA"], days_offset=i) for i in range(3)]
    embs = np.eye(3, dtype=np.float32)
    return evs, embs


def _causal_verdict(conf=0.8):
    return {"relationship": "A_causes_B", "confidence": conf,
            "mechanism": "A가 B를 유발", "direction": "positive"}


def test_infer_pairwise_batch_produces_edges():
    """배치 판정이 전부 A_causes_B(고신뢰) → 후보쌍 수만큼 엣지 + 배치 1회 호출."""
    evs, embs = _three_shared_ticker_events()
    batch_calls = []

    def fake_batch(chunk):
        batch_calls.append(list(chunk))
        return [_causal_verdict() for _ in chunk]

    def fail_single(a, b):
        raise AssertionError("배치 성공 시 단건 호출 금지")

    edges = infer_pairwise(evs, embs, pair_fn=fail_single, batch_pair_fn=fake_batch)
    assert len(edges) == 3                      # 3쌍 전부 인과
    assert all(e.inferred_by == "pairwise_llm" for e in edges)
    assert len(batch_calls) == 1                # 3쌍 ≤ batch_size(6) → 1회
    assert len(batch_calls[0]) == 3


def test_infer_pairwise_filters_non_causal():
    """unrelated/저신뢰 판정은 엣지로 승격되지 않는다."""
    evs, embs = _three_shared_ticker_events()

    def fake_batch(chunk):
        return [
            {"relationship": "unrelated", "confidence": 0.9, "mechanism": "", "direction": "uncertain"},
            {"relationship": "A_causes_B", "confidence": 0.3, "mechanism": "약함", "direction": "positive"},  # 저신뢰
            _causal_verdict(0.75),  # 유일하게 채택
        ]

    edges = infer_pairwise(evs, embs, batch_pair_fn=fake_batch, pair_fn=lambda a, b: {})
    assert len(edges) == 1
    assert edges[0].confidence == 0.75


def test_infer_pairwise_batch_failure_falls_back_to_single():
    """배치 호출이 터지면 단건 pair_fn으로 폴백."""
    evs, embs = _three_shared_ticker_events()
    single_calls = [0]

    def exploding_batch(chunk):
        raise RuntimeError("batch down")

    def counting_single(a, b):
        single_calls[0] += 1
        return _causal_verdict(0.7)

    edges = infer_pairwise(evs, embs, pair_fn=counting_single, batch_pair_fn=exploding_batch)
    assert len(edges) == 3
    assert single_calls[0] == 3          # 3쌍 단건 폴백


def test_infer_pairwise_count_mismatch_falls_back():
    """배치가 개수를 틀리게 반환하면 그 청크는 단건 폴백(매핑 어긋남 방지)."""
    evs, embs = _three_shared_ticker_events()
    single_calls = [0]

    def short_batch(chunk):
        return [_causal_verdict()]        # 3개 요청에 1개만

    def counting_single(a, b):
        single_calls[0] += 1
        return _causal_verdict(0.6)

    edges = infer_pairwise(evs, embs, pair_fn=counting_single, batch_pair_fn=short_batch)
    assert len(edges) == 3
    assert single_calls[0] == 3


def test_infer_pairwise_single_error_skips_only_that_pair():
    """단건 폴백 중 한 쌍이 터져도 나머지는 엣지 생성."""
    evs, embs = _three_shared_ticker_events()

    def exploding_batch(chunk):
        raise RuntimeError("batch down")

    calls = [0]

    def flaky_single(a, b):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("single down")
        return _causal_verdict(0.8)

    edges = infer_pairwise(evs, embs, pair_fn=flaky_single, batch_pair_fn=exploding_batch)
    assert len(edges) == 2               # 3쌍 중 1쌍만 실패


def test_infer_pairwise_no_candidates_returns_empty():
    """후보쌍이 없으면(티커 다르고 유사도 낮음) LLM 호출 없이 빈 결과."""
    a = _ev(0, ["NVDA"], days_offset=0)
    b = _ev(1, ["AAPL"], days_offset=100)
    embs = np.eye(2, dtype=np.float32)

    def must_not_call(*args, **kwargs):
        raise AssertionError("후보 없음 → LLM 호출 금지")

    edges = infer_pairwise([a, b], embs, pair_fn=must_not_call, batch_pair_fn=must_not_call)
    assert edges == []


def test_build_pair_batch_prompt_numbers_pairs():
    evs, _ = _three_shared_ticker_events()
    prompt = _build_pair_batch_prompt([(evs[0], evs[1]), (evs[1], evs[2])])
    assert "===== PAIR 1 =====" in prompt
    assert "===== PAIR 2 =====" in prompt
    assert "EXACTLY 2" in prompt
    assert "EVENT A" in prompt and "EVENT B" in prompt
