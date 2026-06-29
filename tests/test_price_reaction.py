"""M3 Day 3~4 PriceReaction 단위 테스트 (yfinance 호출 없이 FakeProvider 사용)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from src.ingest.schema import Event
from src.score.price_reaction import (
    PriceProvider,
    compute_price_reactions,
    event_reaction,
)


class _FakeProvider(PriceProvider):
    """디스크 접근 없이 메모리 데이터로 동작."""

    def __init__(self, data: dict[str, dict[str, float]]) -> None:
        self._cache = data
        self._dirty = False

    def warm(self, ticker_dates):  # no-op
        pass


def _ev(idx: int, tickers: list[str], occurred: date) -> Event:
    return Event(
        id=f"e{idx}",
        title=f"Event {idx}",
        summary="s",
        occurred_at=datetime(occurred.year, occurred.month, occurred.day, tzinfo=timezone.utc),
        source_urls=[f"http://x.com/{idx}"],
        publishers=["p"],
        tickers_mentioned=tickers,
        spread=2,
    )


def test_event_reaction_simple_one_ticker():
    # NVDA: base 100, 1일 후 110 (10% up), 3일 후 105 (5% up)
    # max(|0.10|, |0.05|) = 0.10
    provider = _FakeProvider(
        {
            "NVDA": {
                "2026-05-10": 100.0,
                "2026-05-11": 110.0,
                "2026-05-13": 105.0,
            }
        }
    )
    ev = _ev(1, ["NVDA"], date(2026, 5, 10))
    r = event_reaction(ev, provider)
    assert abs(r - 0.10) < 1e-6


def test_event_reaction_handles_drop():
    # 큰 하락도 절댓값으로
    provider = _FakeProvider(
        {
            "NVDA": {
                "2026-05-10": 100.0,
                "2026-05-13": 80.0,  # -20%
            }
        }
    )
    ev = _ev(1, ["NVDA"], date(2026, 5, 10))
    r = event_reaction(ev, provider)
    assert abs(r - 0.20) < 1e-6


def test_event_reaction_weights_by_market_cap():
    # NVDA 5% 반응 시총 3조, AAPL 1% 반응 시총 3조 → 가중평균 3%
    provider = _FakeProvider(
        {
            "NVDA": {"2026-05-10": 100.0, "2026-05-11": 105.0},
            "AAPL": {"2026-05-10": 100.0, "2026-05-11": 101.0},
        }
    )
    ev = _ev(1, ["NVDA", "AAPL"], date(2026, 5, 10))
    caps = {"NVDA": 3e12, "AAPL": 3e12}
    r = event_reaction(ev, provider, caps)
    assert abs(r - 0.03) < 1e-3


def test_event_reaction_returns_none_when_no_price_data():
    provider = _FakeProvider({})
    ev = _ev(1, ["NVDA"], date(2026, 5, 10))
    assert event_reaction(ev, provider) is None


def test_compute_price_reactions_normalizes_to_one():
    # 두 이벤트 — 하나 10% 반응, 하나 5% 반응 → 정규화 후 1.0과 0.5
    provider = _FakeProvider(
        {
            "NVDA": {"2026-05-10": 100.0, "2026-05-11": 110.0},
            "AAPL": {"2026-05-10": 100.0, "2026-05-11": 105.0},
        }
    )
    events = [_ev(1, ["NVDA"], date(2026, 5, 10)), _ev(2, ["AAPL"], date(2026, 5, 10))]
    scores = compute_price_reactions(events, provider=provider)
    assert abs(scores["e1"] - 1.0) < 1e-6
    assert abs(scores["e2"] - 0.5) < 1e-6


def test_compute_price_reactions_handles_weekend_with_lookahead():
    # 이벤트 토요일(5/9), 가격은 월요일(5/11)에만 있음 → max 5일 lookahead로 잡힘
    provider = _FakeProvider(
        {"NVDA": {"2026-05-11": 100.0, "2026-05-12": 110.0}}
    )
    ev = _ev(1, ["NVDA"], date(2026, 5, 9))  # 토요일
    scores = compute_price_reactions([ev], provider=provider)
    assert "e1" in scores
