"""M3.5 Day 3 ``macro.fred`` 단위 테스트.

HTTP 호출은 모두 stub. detect_events 로직 + fetch_macro_events 통합 검증.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.macro import fred
from src.macro.fred import MacroObservation


def _obs(d: str, v: float) -> MacroObservation:
    return MacroObservation(date=date.fromisoformat(d), value=v)


# ----- detect_events --------------------------------------------------------


def test_too_few_observations_returns_empty():
    assert fred.detect_events("DGS10", []) == []
    assert fred.detect_events("DGS10", [_obs("2026-01-01", 4.0)]) == []
    assert fred.detect_events("DGS10", [_obs("2026-01-01", 4.0), _obs("2026-01-02", 4.1)]) == []


def test_zero_variance_returns_empty():
    obs = [_obs(f"2026-01-{d:02d}", 4.0) for d in range(1, 10)]
    assert fred.detect_events("DGS10", obs) == []


def test_one_sigma_change_emitted():
    """평소 ±0.01 변동이다가 +0.05 튀면 5σ 정도 → emit."""
    base = 4.0
    obs = []
    # 처음 10일 작은 변동
    for i in range(10):
        obs.append(_obs(f"2026-01-{i+1:02d}", base + (0.01 if i % 2 == 0 else -0.01)))
    # 11일째 큰 점프
    obs.append(_obs("2026-01-11", base + 0.10))
    events = fred.detect_events("DGS10", obs, sigma_threshold=1.0)
    assert len(events) == 1
    ev = events[0]
    assert ev.series_id == "DGS10"
    assert ev.value == pytest.approx(base + 0.10)
    assert ev.sigma_z > 2  # 잡힐 정도면 1 이상이지만 안전마진 2
    assert "10년물 국채금리" in ev.summary_ko
    assert "+0.11%p" in ev.summary_ko or "+0.10%p" in ev.summary_ko  # 반올림 허용
    assert ev.id == "macro_DGS10_20260111"


def test_below_threshold_not_emitted():
    """threshold 2σ 인데 변화가 1.5σ면 emit 안 됨."""
    base = 4.0
    obs = []
    for i in range(10):
        obs.append(_obs(f"2026-01-{i+1:02d}", base + (0.01 if i % 2 == 0 else -0.01)))
    obs.append(_obs("2026-01-11", base + 0.10))  # 큰 점프지만
    events = fred.detect_events("DGS10", obs, sigma_threshold=10.0)  # threshold 매우 높게
    assert events == []


def test_negative_change_emitted_with_negative_sigma():
    base = 5.0
    obs = []
    for i in range(10):
        obs.append(_obs(f"2026-01-{i+1:02d}", base + (0.01 if i % 2 == 0 else -0.01)))
    obs.append(_obs("2026-01-11", base - 0.10))  # 큰 하락
    events = fred.detect_events("DGS10", obs, sigma_threshold=1.0)
    assert len(events) == 1
    assert events[0].sigma_z < 0
    assert events[0].is_negative


def test_emit_after_filters_early_events():
    """emit_after 이전 큰 변화는 detect 되지만 emit 안 됨."""
    base = 4.0
    obs = [_obs(f"2026-01-{i+1:02d}", base + (0.01 if i % 2 == 0 else -0.01)) for i in range(10)]
    obs.append(_obs("2026-01-15", base + 0.10))  # 1월 큰 변화
    obs.append(_obs("2026-01-16", base + 0.10))  # 안정
    obs.append(_obs("2026-02-10", base + 0.20))  # 2월 더 큰 변화
    events = fred.detect_events(
        "DGS10", obs, sigma_threshold=1.0, emit_after=date(2026, 2, 1)
    )
    # 2월 변화만 남아야 함
    assert len(events) == 1
    assert events[0].observed_at.date() == date(2026, 2, 10)


def test_summary_format_for_percent_series():
    obs = [_obs(f"2026-01-{i+1:02d}", 4.0 + (0.005 if i % 2 == 0 else -0.005)) for i in range(10)]
    obs.append(_obs("2026-01-11", 4.25))
    events = fred.detect_events("FEDFUNDS", obs, sigma_threshold=1.0)
    assert len(events) == 1
    s = events[0].summary_ko
    assert "연방기금금리" in s
    assert "4.00%" in s
    assert "4.25%" in s
    assert "σ" in s


def test_summary_format_for_dollar_series():
    # 마지막 obs (인덱스 10) 의 직전 (인덱스 9, 홀수) → 80.0 - 0.1 = 79.9
    obs = [_obs(f"2026-01-{i+1:02d}", 80.0 + (0.1 if i % 2 == 0 else -0.1)) for i in range(10)]
    obs.append(_obs("2026-01-11", 85.0))
    events = fred.detect_events("DCOILWTICO", obs, sigma_threshold=1.0)
    assert len(events) == 1
    s = events[0].summary_ko
    assert "WTI 유가" in s
    assert "$79.90" in s  # prev (alternating 의 직전)
    assert "$85.00" in s  # current


# ----- fetch_macro_events ---------------------------------------------------


def test_fetch_macro_events_aggregates_all_series():
    """fetch_fn 주입으로 HTTP 없이 통합 검증."""
    fake_data = {
        "FEDFUNDS": [
            _obs(f"2026-01-{i+1:02d}", 4.0) for i in range(20)
        ] + [_obs("2026-05-25", 3.75)],
        "DGS10": [
            _obs(f"2026-01-{i+1:02d}", 4.5 + (0.01 if i % 2 == 0 else -0.01))
            for i in range(20)
        ] + [_obs("2026-05-26", 4.80)],
    }

    def fake_fetch(sid, lookback_days):
        return fake_data.get(sid, [])

    events = fred.fetch_macro_events(
        series_ids=["FEDFUNDS", "DGS10", "VIXCLS"],  # VIXCLS 는 빈 데이터
        emit_days=365 * 2,  # 모든 변화 emit
        fetch_fn=fake_fetch,
    )
    assert len(events) == 2
    # 최신순 정렬
    assert events[0].observed_at >= events[1].observed_at
    series_ids = {e.series_id for e in events}
    assert series_ids == {"FEDFUNDS", "DGS10"}


def test_fetch_macro_events_continues_on_single_series_error():
    """1개 시리즈 fetch 실패해도 나머지는 진행."""

    def flaky_fetch(sid, lookback_days):
        if sid == "DGS10":
            raise RuntimeError("network timeout")
        return [_obs(f"2026-01-{i+1:02d}", 4.0) for i in range(10)] + [
            _obs("2026-05-25", 5.0)
        ]

    events = fred.fetch_macro_events(
        series_ids=["FEDFUNDS", "DGS10"],
        emit_days=365 * 2,
        fetch_fn=flaky_fetch,
    )
    assert all(e.series_id == "FEDFUNDS" for e in events)
    assert len(events) >= 1


def test_fetch_macro_events_missing_key_raises():
    """MissingFredKeyError 는 retry 의미 없어 raise 그대로 전파."""

    def keyless_fetch(sid, lookback_days):
        raise fred.MissingFredKeyError("no key")

    with pytest.raises(fred.MissingFredKeyError):
        fred.fetch_macro_events(series_ids=["FEDFUNDS"], fetch_fn=keyless_fetch)


# ----- SERIES_META ----------------------------------------------------------


def test_all_series_have_korean_label_and_unit():
    for sid, meta in fred.SERIES_META.items():
        assert "label_ko" in meta and meta["label_ko"]
        assert "unit" in meta and meta["unit"]
        assert "freq" in meta
