"""시장 수급·컨센서스 스냅샷 소스 — 티커 기반 결정론 수집 (LLM 0콜).

배경(2026-07-20 INDI 비교 감사): 타 AI 리포트는 공매도 비율·목표주가
컨센서스·애널리스트 등급 같은 시장 수급 데이터를 담았지만 FinVision은
웹 문서 수집만 해서 이 축이 통째로 없었다. 이 소스는 구조화 API에서
직접 가져와 스냅샷 문서 1건을 만든다:

- Yahoo Finance quoteSummary(앱 공용 yfinance_client 재사용 — crumb 세션
  관리 포함): 주가·시총·52주 범위·공매도(비율/전월대비)·목표주가·추천 평균
  (yfinance 라이브러리는 crumb 없는 호출로 429가 나서 쓰지 않는다 — 실측)
- Finnhub(무료 티어): 애널리스트 추천 트렌드 최근 3개월

전 항목 '데이터 기준일'과 출처를 명시. 일부 실패는 부분 문서(fail-soft),
전부 실패면 None. 집계·지연 데이터임을 문서에 명시한다(정직성).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.deep_research.models import ExtractedContent, SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

_REC_KEY_KO = {
    "strong_buy": "적극 매수", "buy": "매수", "hold": "중립",
    "sell": "매도", "strong_sell": "적극 매도", "underperform": "시장수익률 하회",
    "outperform": "시장수익률 상회", "none": "없음",
}


# ── 순수 포매터 (테스트 대상, 네트워크 없음) ────────────────────────

def _fmt_money(v) -> Optional[str]:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.2f}"


def _fmt_int(v) -> Optional[str]:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return None


def _fmt_pct_frac(v) -> Optional[str]:
    """0.3049 → '30.49%' (yfinance 비율 필드는 소수)."""
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return None


def build_market_snapshot_text(
    ticker: str, info: dict, rec_trends: list[dict],
    as_of: Optional[str] = None,
) -> str:
    """yfinance info + Finnhub 추천 트렌드 → 스냅샷 문서. 값 없으면 항목 생략."""
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = info.get("longName") or info.get("shortName") or ticker

    def _row(label: str, value: Optional[str]) -> list[str]:
        return [f"- {label}: {value}"] if value else []

    size_rows = (
        _row("현재가", _fmt_money(info.get("currentPrice")
                               or info.get("regularMarketPrice")))
        + _row("시가총액", _fmt_money(info.get("marketCap")))
    )
    lo, hi = _fmt_money(info.get("fiftyTwoWeekLow")), _fmt_money(
        info.get("fiftyTwoWeekHigh"))
    if lo and hi:
        size_rows.append(f"- 52주 범위: {lo} ~ {hi}")
    size_rows += (_row("발행주식수", _fmt_int(info.get("sharesOutstanding")))
                  + _row("유동주식수", _fmt_int(info.get("floatShares"))))

    short_rows = (
        _row("공매도 잔고", _fmt_int(info.get("sharesShort")))
        + _row("공매도 잔고(전월)", _fmt_int(info.get("sharesShortPriorMonth")))
        + _row("유동주식 대비 공매도 비율",
               _fmt_pct_frac(info.get("shortPercentOfFloat")))
    )
    sr = info.get("shortRatio")
    if sr:
        short_rows.append(f"- 공매도 커버 소요일(Short Ratio): {sr}일")

    analyst_rows = _row("목표주가 평균", _fmt_money(info.get("targetMeanPrice")))
    tlo, thi = _fmt_money(info.get("targetLowPrice")), _fmt_money(
        info.get("targetHighPrice"))
    if tlo and thi:
        analyst_rows.append(f"- 목표주가 범위: {tlo} ~ {thi}")
    n = info.get("numberOfAnalystOpinions")
    if n:
        analyst_rows.append(f"- 커버리지 애널리스트 수: {n}명")
    rec = info.get("recommendationKey")
    if rec and rec != "none":
        mean = info.get("recommendationMean")
        mean_str = f" (평균 {mean}, 1=적극매수~5=적극매도)" if mean else ""
        analyst_rows.append(f"- 종합 추천: {_REC_KEY_KO.get(rec, rec)}{mean_str}")

    trend_rows = [
        f"- {t.get('period', '')}: 적극매수 {t.get('strongBuy', 0)} / "
        f"매수 {t.get('buy', 0)} / 중립 {t.get('hold', 0)} / "
        f"매도 {t.get('sell', 0)} / 적극매도 {t.get('strongSell', 0)}"
        for t in rec_trends[:3]
    ]

    lines = [
        f"【시장 수급·컨센서스 스냅샷 — {name} ({ticker})】",
        f"데이터 기준일: {as_of} (집계·지연 데이터 — 실시간 아님)",
        "출처: Yahoo Finance 집계, Finnhub(추천 트렌드)",
    ]
    # 값 없는 섹션은 헤더째 생략 (수집 실패 축을 빈 제목으로 위장하지 않는다)
    for header, rows in (
        ("[주가·규모]", size_rows),
        ("[공매도 수급]", short_rows),
        ("[애널리스트 컨센서스]", analyst_rows),
        ("[추천 트렌드 — Finnhub, 최근 월별]", trend_rows),
    ):
        if rows:
            lines.append("")
            lines.append(header)
            lines.extend(rows)
    return "\n".join(lines)


def has_market_data(text: str) -> bool:
    """스냅샷에 실데이터 항목('- ')이 하나라도 있는지."""
    return "\n- " in text


# ── 소스 본체 ──────────────────────────────────────────────────────

class MarketContextSource(BaseSource):
    """티커 → 시장 수급 스냅샷 문서 1건. 전부 구조화 API, LLM 0콜."""

    source_type = "market_context"

    def is_available(self) -> bool:
        return True  # yfinance는 키 불필요, Finnhub는 있으면 추가

    async def search(self, query: str, **kwargs) -> list[SearchResult]:
        return []  # collect()가 진입점

    async def collect(self, ticker: str) -> Optional[ExtractedContent]:
        ticker = (ticker or "").strip().upper()
        if not ticker:
            return None
        try:
            info, rec = await asyncio.gather(
                asyncio.to_thread(self._yf_info, ticker),
                asyncio.to_thread(self._finnhub_rec, ticker),
            )
            if not info and not rec:
                logger.info(f"[market_context] {ticker} 시장 데이터 없음")
                return None
            text = build_market_snapshot_text(ticker, info or {}, rec or [])
            if not has_market_data(text):
                return None
            doc = ExtractedContent(
                url=f"https://finance.yahoo.com/quote/{ticker}",
                title=f"[시장 수급 스냅샷] {ticker} — 공매도·목표주가·추천 트렌드",
                content=text,
                domain="finance.yahoo.com",
                word_count=len(text.split()),
            )
            logger.info(f"[market_context] {ticker} 스냅샷 {len(text)}자 생성")
            return doc
        except Exception as e:
            logger.warning(f"[market_context] {ticker} 수집 예외: {e}")
            return None

    # quoteSummary 모듈별로 평탄화해 가져올 키
    _QS_KEYS = {
        "financialData": (
            "currentPrice", "targetMeanPrice", "targetLowPrice",
            "targetHighPrice", "numberOfAnalystOpinions",
            "recommendationKey", "recommendationMean",
        ),
        "defaultKeyStatistics": (
            "sharesOutstanding", "floatShares", "sharesShort",
            "sharesShortPriorMonth", "shortRatio", "shortPercentOfFloat",
        ),
        "summaryDetail": ("marketCap", "fiftyTwoWeekLow", "fiftyTwoWeekHigh"),
        "quoteType": ("longName", "shortName"),
    }

    @classmethod
    def _yf_info(cls, ticker: str) -> dict:
        """앱 공용 quoteSummary 클라이언트(crumb 세션) → 평탄 dict.

        Yahoo 값은 {"raw": x, "fmt": "..."} 래핑 — raw만 취한다.
        """
        try:
            from app.services.yfinance_client import _yf_quoteSummary
            qs = _yf_quoteSummary(
                ticker,
                modules="financialData,defaultKeyStatistics,"
                        "summaryDetail,quoteType",
            ) or {}
        except Exception as e:
            logger.debug(f"[market_context] quoteSummary 실패 {ticker}: {e}")
            return {}
        flat: dict = {}
        for module, keys in cls._QS_KEYS.items():
            mod = qs.get(module) or {}
            for k in keys:
                v = mod.get(k)
                if isinstance(v, dict):
                    v = v.get("raw")
                if v not in (None, "", {}):
                    flat[k] = v
        return flat

    @staticmethod
    def _finnhub_rec(ticker: str) -> list[dict]:
        try:
            from app.services.finnhub_client import _get_client, _rate_limit
            client = _get_client()
            if not client:
                return []
            _rate_limit()
            data = client.recommendation_trends(ticker)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug(f"[market_context] finnhub 실패 {ticker}: {e}")
            return []
