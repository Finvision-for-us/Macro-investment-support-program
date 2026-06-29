"""영향력 점수 단위 테스트 — 3 신호 (mcap 제거 후, 2026-05-30~)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from src.ingest.schema import Event
from src.score.impact import (
    NEUTRAL_DEFAULT,
    PR_WIRE_ONLY_DISCOUNT,
    SINGLE_PUBLISHER_DISCOUNT,
    TWO_PUBLISHER_DISCOUNT,
    discount_factor,
    effective_spread,
    is_pr_wire,
    score_events,
    spread_score,
)


def _event(id: str, spread: int, publishers: list[str]) -> Event:
    return Event(
        id=id,
        title=f"Event {id}",
        summary="...",
        occurred_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        source_urls=[f"https://x/{i}" for i in range(spread)],
        publishers=publishers,
        tickers_mentioned=["NVDA"],
        spread=spread,
    )


# ----- spread_score (그대로 유지) -------------------------------------------


def test_spread_score_singleton_is_zero():
    assert spread_score(1, max_spread=10) == 0.0


def test_spread_score_max_is_one():
    assert math.isclose(spread_score(10, max_spread=10), 1.0)


def test_spread_score_monotonic():
    s = [spread_score(i, max_spread=20) for i in range(1, 21)]
    assert all(s[i] <= s[i + 1] for i in range(len(s) - 1))


# ----- PR wire 검출 ---------------------------------------------------------


def test_is_pr_wire_detects_known_wires():
    assert is_pr_wire("GlobeNewswire Inc.")
    assert is_pr_wire("Business Wire")
    assert is_pr_wire("PR Newswire")
    assert is_pr_wire("PRNewswire")
    assert is_pr_wire("ACCESSWIRE")
    assert is_pr_wire("Cision PR Newswire")


def test_is_pr_wire_misses_normal_publishers():
    assert not is_pr_wire("Reuters")
    assert not is_pr_wire("The Motley Fool")
    assert not is_pr_wire("Benzinga")
    assert not is_pr_wire("Investing.com")
    assert not is_pr_wire("Bloomberg")


def test_is_pr_wire_empty_safe():
    assert not is_pr_wire("")


# ----- discount_factor ------------------------------------------------------


def test_three_normal_publishers_no_discount():
    assert discount_factor(["Reuters", "Bloomberg", "Benzinga"]) == 1.0


def test_single_publisher_strong_discount():
    assert discount_factor(["The Motley Fool"]) == SINGLE_PUBLISHER_DISCOUNT


def test_single_publisher_pr_wire_strongest_discount():
    """단일 publisher 가 PR wire → 가장 강한 디스카운트 (all_wires 조건 우선)."""
    assert discount_factor(["GlobeNewswire Inc."]) == PR_WIRE_ONLY_DISCOUNT


def test_two_publishers_normal_mild_discount():
    assert discount_factor(["Reuters", "Bloomberg"]) == TWO_PUBLISHER_DISCOUNT


def test_two_publishers_one_wire_intermediate():
    assert discount_factor(["GlobeNewswire Inc.", "Reuters"]) == 0.6


def test_all_wires_strongest_discount():
    assert (
        discount_factor(["GlobeNewswire Inc.", "Business Wire", "PRNewswire"])
        == PR_WIRE_ONLY_DISCOUNT
    )


def test_many_publishers_with_few_wires_mild_discount():
    factor = discount_factor(
        ["Reuters", "Bloomberg", "Benzinga", "Fool", "GlobeNewswire Inc."]
    )
    assert 0.8 <= factor < 1.0


def test_empty_publishers_treated_as_single():
    assert discount_factor([]) == SINGLE_PUBLISHER_DISCOUNT


def test_duplicate_publishers_dedupe():
    """같은 publisher 가 여러 번 들어와도 unique 로 본다 — 단일 publisher 로 취급."""
    assert (
        discount_factor(["Reuters", "Reuters", "Reuters"])
        == SINGLE_PUBLISHER_DISCOUNT
    )


# ----- effective_spread -----------------------------------------------------


def test_effective_spread_three_publishers():
    eff, factor = effective_spread(10, ["A", "B", "C"])
    assert eff == 10.0
    assert factor == 1.0


def test_effective_spread_pr_wire_only():
    eff, factor = effective_spread(11, ["GlobeNewswire Inc."])
    assert factor == PR_WIRE_ONLY_DISCOUNT
    # 11 × 0.25 = 2.75, 최소 1 보존이라 max(2.75, 1) = 2.75
    assert math.isclose(eff, 11 * PR_WIRE_ONLY_DISCOUNT)


def test_effective_spread_zero_is_safe():
    eff, factor = effective_spread(0, [])
    assert eff == 0.0
    assert factor == 1.0


def test_effective_spread_minimum_1():
    """디스카운트 결과 < 1 이면 1 로 클램프 (log 함수 안전)."""
    eff, _ = effective_spread(2, ["GlobeNewswire Inc."])
    # 2 × 0.25 = 0.5 → 1 로 클램프
    assert eff == 1.0


# ----- score_events 통합 ----------------------------------------------------


def test_score_events_empty():
    assert score_events([]) == []


def test_pr_wire_single_publisher_demoted_vs_normal_multi_publisher():
    """단일 wire 보도(spread 11)가 정상 보도(spread 5) 보다 낮은 점수여야 함."""
    suspect = _event("bad", spread=11, publishers=["GlobeNewswire Inc."])
    real = _event("good", spread=5, publishers=["Reuters", "Bloomberg", "Benzinga"])

    out = score_events([suspect, real])
    rank = {s.event.id: i for i, s in enumerate(out)}
    assert rank["good"] < rank["bad"], "정상 다양한 보도가 wire 단일보다 위여야 함"


def test_score_events_includes_diagnostic_fields():
    suspect = _event("bad", spread=11, publishers=["GlobeNewswire Inc."])
    out = score_events([suspect])
    s = out[0]
    assert s.effective_spread < suspect.spread  # 디스카운트 적용됨
    assert s.spread_discount_factor == PR_WIRE_ONLY_DISCOUNT


def test_score_events_neutral_default_when_missing_signals():
    """novelty/pr 인자 안 주면 NEUTRAL_DEFAULT (0.5) 적용."""
    ev = _event("solo", spread=5, publishers=["Reuters", "Bloomberg"])
    out = score_events([ev])
    assert out[0].novelty_score == NEUTRAL_DEFAULT
    assert out[0].price_reaction_score == NEUTRAL_DEFAULT


def test_score_events_uses_provided_signals():
    ev = _event("e1", spread=5, publishers=["Reuters", "Bloomberg"])
    out = score_events(
        [ev],
        novelty_scores={"e1": 1.0},
        price_reaction_scores={"e1": 0.8},
    )
    assert out[0].novelty_score == 1.0
    assert out[0].price_reaction_score == 0.8


def test_score_events_weights_sum_to_one():
    """모든 신호 1.0 이면 impact = 1.0 (가중치 합 = 1)."""
    ev = _event("max", spread=10, publishers=["Reuters", "Bloomberg", "Benzinga"])
    out = score_events(
        [ev],
        novelty_scores={"max": 1.0},
        price_reaction_scores={"max": 1.0},
    )
    # spread_score 는 자기 자신이 max 라 1.0
    assert math.isclose(out[0].impact_score, 1.0, abs_tol=1e-6)
