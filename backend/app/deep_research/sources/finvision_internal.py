from __future__ import annotations
import asyncio
import logging
from typing import Optional

from app.deep_research.models import SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)


class FinVisionInternalSource(BaseSource):
    """FinVision 내부 데이터 소스 — 기존 서비스를 직접 호출."""

    source_type = "finvision_internal"

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, **kwargs) -> list[SearchResult]:
        return []  # 직접 검색 아님

    async def fetch_stock_context(self, ticker: str) -> str:
        """티커에 대한 모든 FinVision 내부 데이터를 문자열로 반환."""
        parts: list[str] = []

        results = await asyncio.gather(
            self._get_overview(ticker),
            self._get_financials(ticker),
            self._get_earnings(ticker),
            self._get_guidance(ticker),
            self._get_news(ticker),
            self._get_filings(ticker),
            return_exceptions=True,
        )

        labels = ["### 종목 개요", "### 재무 데이터", "### 어닝 히스토리",
                  "### 가이던스 분석", "### 최신 뉴스", "### SEC 공시"]
        for label, result in zip(labels, results):
            if isinstance(result, str) and result.strip():
                parts.append(f"{label}\n{result}")

        return "\n\n".join(parts)

    async def _get_overview(self, ticker: str) -> str:
        try:
            from app.services import yfinance_client
            data = await asyncio.to_thread(yfinance_client.get_overview, ticker)
            if not data:
                return ""
            div_yield = data.get("dividend_yield") or 0
            lines = [
                f"회사명: {data.get('name', ticker)}",
                f"섹터: {data.get('sector', 'N/A')} / 산업: {data.get('industry', 'N/A')}",
                f"현재가: ${data.get('current_price', 'N/A')}",
                f"시가총액: ${data.get('market_cap', 0):,.0f}",
                f"52주 최고/최저: ${data.get('52w_high', 'N/A')} / ${data.get('52w_low', 'N/A')}",
                f"PER: {data.get('pe_ratio', 'N/A')} / PBR: {data.get('pb_ratio', 'N/A')}",
                f"EPS: {data.get('eps', 'N/A')} / ROE: {data.get('roe', 'N/A')}",
                f"영업이익률: {data.get('operating_margin', 'N/A')} / 순이익률: {data.get('profit_margin', 'N/A')}",
                f"배당수익률: {div_yield * 100:.2f}%" if div_yield else "",
                f"베타: {data.get('beta', 'N/A')}",
                f"사업 요약: {str(data.get('description', ''))[:500]}",
            ]
            return "\n".join(l for l in lines if l)
        except Exception as e:
            logger.debug(f"[internal] overview 실패: {e}")
            return ""

    async def _get_financials(self, ticker: str) -> str:
        try:
            from app.services import yfinance_client
            data = await asyncio.to_thread(yfinance_client.get_financials, ticker)
            if not data:
                return ""
            income = data.get("income_statement", [])
            if not income:
                return ""
            lines = []
            for row in income[:4]:  # 최근 4분기
                lines.append(
                    f"{row.get('date', '')}: 매출 ${row.get('revenue', 0):,.0f} / "
                    f"순이익 ${row.get('net_income', 0):,.0f} / "
                    f"영업이익률 {row.get('operating_margin', 0):.1f}%"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[internal] financials 실패: {e}")
            return ""

    async def _get_earnings(self, ticker: str) -> str:
        try:
            from app.services.earnings_analyzer import get_earnings_with_reactions
            from app.database import DB_PATH
            import aiosqlite
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT period_end, report_date, eps_actual, eps_estimate, "
                    "surprise_pct, reaction_1d_change FROM earnings_surprises "
                    "WHERE ticker=? ORDER BY report_date DESC LIMIT 8",
                    (ticker,)
                )
                rows = await cursor.fetchall()
            if not rows:
                return ""
            lines = []
            for r in rows:
                surprise = r["surprise_pct"]
                reaction = r["reaction_1d_change"]
                lines.append(
                    f"{r['report_date']}: EPS {r['eps_actual']} (예상 {r['eps_estimate']}) "
                    f"서프라이즈 {surprise:+.1f}% / 주가반응 {reaction:+.1f}%"
                    if surprise is not None and reaction is not None
                    else f"{r['report_date']}: EPS {r['eps_actual']} (예상 {r['eps_estimate']})"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[internal] earnings 실패: {e}")
            return ""

    async def _get_guidance(self, ticker: str) -> str:
        try:
            from app.services.gemini_guidance import _get_cached_guidance
            cached = await _get_cached_guidance(ticker)
            if not cached:
                return ""
            lines = []
            for g in cached[:4]:
                lines.append(
                    f"{g.get('period_end', '')}: {g.get('guidance_summary', '')[:200]} "
                    f"(감성점수 {g.get('sentiment_score', 'N/A')})"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[internal] guidance 실패: {e}")
            return ""

    async def _get_news(self, ticker: str) -> str:
        try:
            from app.services.news_client import get_stock_news
            news = await asyncio.to_thread(get_stock_news, ticker)
            if not news:
                return ""
            lines = []
            for n in news[:5]:
                lines.append(f"- {n.get('datetime', '')} [{n.get('source', '')}] {n.get('headline', '')}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[internal] news 실패: {e}")
            return ""

    async def _get_filings(self, ticker: str) -> str:
        try:
            from app.services.sec_client import get_filings
            filings = await asyncio.to_thread(get_filings, ticker)
            if not filings:
                return ""
            lines = []
            for f in filings[:5]:
                lines.append(f"- {f.get('date', '')} [{f.get('type', '')}] {f.get('description', '')}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[internal] filings 실패: {e}")
            return ""
