"""저지연 시장 데이터 소스 (Yahoo Finance) — 일간 거시 지표용.

FRED 는 일간 시장 지표(VIX·WTI·10년물·달러엔)를 1~2일 지연 발행한다. 이 모듈은
동일 지표를 Yahoo 에서 당일~1일 신선도로 받아 :class:`MacroObservation` list 로 반환해,
``fred.fetch_latest_events`` 가 우선 소스로 쓰게 한다(실패 시 FRED 폴백).

지원하지 않는 시리즈(월간 지표, 장단기 금리차 등)는 빈 list 를 반환 → 호출측이 FRED 로 폴백.
장단기 금리차(T10Y2Y)는 Yahoo 에 현물 2년물이 없어(선물뿐, 방법론 상이) 제외 — FRED 정본 유지.
"""
from __future__ import annotations

from datetime import date, timedelta

from src.macro.fred import MacroObservation

# FRED series_id → Yahoo 티커 (현물/지수에 가장 근접한 것)
YAHOO_TICKERS: dict[str, str] = {
    "VIXCLS": "^VIX",       # CBOE 변동성지수
    "DCOILWTICO": "CL=F",   # WTI 원유 (근월 선물 — 현물 근사)
    "DGS10": "^TNX",        # 10년물 국채 수익률
    "DEXJPUS": "JPY=X",     # USD/JPY (엔/달러)
}

DEFAULT_LOOKBACK_DAYS = 90  # σ 계산 + 최신치 확보용 히스토리


def fetch_yahoo_observations(
    series_id: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[MacroObservation]:
    """Yahoo 에서 시리즈의 최근 관측치를 오름차순 :class:`MacroObservation` list 로 반환.

    지원하지 않는 시리즈이거나 데이터가 없으면 **빈 list** 를 반환(호출측 FRED 폴백).
    네트워크/파싱 오류는 그대로 전파 — 호출측(``fetch_latest_events``)에서 잡아 폴백한다.
    """
    ticker = YAHOO_TICKERS.get(series_id)
    if not ticker:
        return []

    import yfinance as yf  # 지연 임포트 — 테스트/FRED-only 경로에 부담 X

    end = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=lookback_days)
    hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    if hist is None or len(hist) == 0:
        return []

    out: list[MacroObservation] = []
    for idx, row in hist.iterrows():
        value = row.get("Close")
        if value is None or value != value:  # NaN 방어
            continue
        out.append(MacroObservation(date=idx.date(), value=float(value)))
    return out
