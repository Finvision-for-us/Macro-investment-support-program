"""PriceReaction 신호: 사건 후 1일/3일 영향 종목 수익률 절댓값.

목적: 시장이 이미 크게 반응한 사건을 부각.
- 절댓값 사용: 호재/악재 방향은 다른 곳에서, 여기는 크기만.
- 다종목 가중: 시총 가중 평균 (큰 회사 반응이 더 의미 큼).
- 배치 내 정규화: max로 나눠 0~1.

구현:
1. 모든 (ticker, event_date) 쌍 수집
2. yfinance bulk fetch → 디스크 캐시 (영구, TTL 없음)
3. 각 이벤트별 max(|1d return|, |3d return|) × 시총 가중
4. 배치 내 max로 정규화
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

from src.config import OUTPUTS_DIR
from src.ingest.schema import Event

PRICE_CACHE_PATH = OUTPUTS_DIR / "price_cache.json"
WINDOWS = (1, 3)  # 사건 후 며칠
MAX_TICKERS_PER_EVENT = 20
PRICE_LOOKUP_MAX_DAYS_FORWARD = 5  # 주말 등으로 가격 없으면 며칠까지 앞으로 검색


class PriceProvider:
    """디스크 캐시 기반 historical close 가격 제공자."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, float]] = self._load()
        self._dirty = False

    def _load(self) -> dict[str, dict[str, float]]:
        if not PRICE_CACHE_PATH.exists():
            return {}
        try:
            return json.loads(PRICE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def save(self) -> None:
        if self._dirty:
            PRICE_CACHE_PATH.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False

    def warm(self, ticker_dates: list[tuple[str, date]]) -> None:
        """필요한 (ticker, date) 모음에서 unique ticker마다 1회 yfinance fetch."""
        ticker_to_dates: dict[str, list[date]] = {}
        for t, d in ticker_dates:
            ticker_to_dates.setdefault(t, []).append(d)

        for t, dates in ticker_to_dates.items():
            min_d = min(dates) - timedelta(days=3)
            max_d = max(dates) + timedelta(days=max(WINDOWS) + 3)

            # 이미 캐시에 모든 날짜 있으면 skip
            existing = self._cache.get(t, {})
            need_fetch = False
            cur = min_d
            while cur <= max_d:
                if cur.isoformat() not in existing:
                    need_fetch = True
                    break
                cur += timedelta(days=1)
            if not need_fetch:
                continue

            try:
                hist = yf.Ticker(t).history(
                    start=min_d.isoformat(), end=max_d.isoformat()
                )
            except Exception:  # noqa: BLE001
                continue
            if hist.empty:
                continue

            self._cache.setdefault(t, {})
            for idx, row in hist.iterrows():
                try:
                    self._cache[t][idx.date().isoformat()] = float(row["Close"])
                    self._dirty = True
                except (KeyError, ValueError):
                    continue
        self.save()

    def price_on_or_after(
        self,
        ticker: str,
        d: date,
        max_days: int = PRICE_LOOKUP_MAX_DAYS_FORWARD,
    ) -> float | None:
        """`d` 또는 그 후 첫 거래일의 종가."""
        cache = self._cache.get(ticker, {})
        for offset in range(max_days + 1):
            key = (d + timedelta(days=offset)).isoformat()
            if key in cache:
                return cache[key]
        return None


def event_reaction(
    event: Event,
    provider: PriceProvider,
    ticker_caps: dict[str, float] | None = None,
) -> float | None:
    """이벤트 1건의 PriceReaction raw 값 (정규화 전).

    Returns None if no price data for any ticker.
    """
    event_date = event.occurred_at.date()
    reactions: dict[str, float] = {}

    for ticker in event.tickers_mentioned[:MAX_TICKERS_PER_EVENT]:
        base = provider.price_on_or_after(ticker, event_date)
        if base is None or base <= 0:
            continue
        max_ret = 0.0
        for window in WINDOWS:
            future = provider.price_on_or_after(
                ticker, event_date + timedelta(days=window)
            )
            if future is None:
                continue
            ret = abs((future - base) / base)
            max_ret = max(max_ret, ret)
        if max_ret > 0:
            reactions[ticker] = max_ret

    if not reactions:
        return None

    if ticker_caps:
        weights = {t: ticker_caps.get(t, 0.0) for t in reactions}
        total_w = sum(weights.values())
        if total_w > 0:
            return sum(r * weights[t] for t, r in reactions.items()) / total_w

    return sum(reactions.values()) / len(reactions)


def compute_price_reactions(
    events: list[Event],
    ticker_caps: dict[str, float] | None = None,
    provider: PriceProvider | None = None,
) -> dict[str, float]:
    """이벤트별 PriceReaction 점수 (0~1, 배치 max로 정규화)."""
    if not events:
        return {}

    provider = provider or PriceProvider()
    needed: list[tuple[str, date]] = []
    for ev in events:
        for t in ev.tickers_mentioned[:MAX_TICKERS_PER_EVENT]:
            needed.append((t, ev.occurred_at.date()))
    if needed:
        provider.warm(needed)

    raw: dict[str, float] = {}
    for ev in events:
        r = event_reaction(ev, provider, ticker_caps)
        if r is not None:
            raw[ev.id] = r

    if not raw:
        return {}

    max_r = max(raw.values())
    if max_r <= 0:
        return {ev_id: 0.0 for ev_id in raw}

    return {ev_id: min(1.0, r / max_r) for ev_id, r in raw.items()}
