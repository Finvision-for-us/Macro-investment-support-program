"""Gemini AI 기반 종목별 프로필 분석 서비스

각 종목에 대해:
1. 사업 영역별 실제 경쟁사 선정
2. 해당 종목에서 중요한 핵심 지표 선정

결과는 DB에 영구 캐싱 (한 번 분석하면 재분석 안 함).
"""

import aiosqlite
import asyncio
import json
import logging
import requests
from datetime import datetime

from app.config import GOOGLE_API_KEY
from app.database import DB_PATH

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash-lite"

PROFILE_PROMPT = """[역할] 종목 프로파일링 전문가. 사업 구조를 분석하여 직접 경쟁사를 식별하고, 해당 종목에 최적화된 분석 지표를 선정.

[대상] {ticker} ({company_name}) | {sector} > {industry} | 시총 {market_cap}
[사업] {description}

[재무 컨텍스트 — key metric 선택 참고용 보조 정보]
{financial_context}

[Task 1: 경쟁사] 사업 영역별 직접 경쟁사 선정
- 직접 경쟁만 (간접 제외). 예: Apple 스마트폰 → 삼성, 샤오미 (O) / Microsoft (X)
- NYSE/NASDAQ 상장 종목만 (ADR 가능: TSM, BABA 등)
- 비상장 제외. 영역당 2-3개, 총 5-8개

[Task 2: 핵심지표] 이 종목만의 핵심 분석 지표 7-10개
같은 산업이어도 회사마다 다름. 성장주→revenue_growth, 배당주→dividend_yield
선택 가능 목록: pe_ratio, forward_pe, pb_ratio, ev_to_ebitda, profit_margin, operating_margin, gross_margin, roe, roa, roic, total_revenue, net_income, ebitda, debt_to_equity, current_ratio, total_debt, total_cash, asset_turnover, inventory_turnover, ocf_margin, capex_to_revenue, revenue_per_share, dividend_yield, payout_ratio, revenue_growth, eps_growth, net_income_growth, operating_income_growth, beta, fcf

[재무 컨텍스트 사용 정책]
- 위 재무 컨텍스트는 key metric 선택을 돕는 참고 정보다.
- reason에는 구체적인 숫자 값을 직접 쓰지 말 것.
- 수치가 높거나 낮다는 이유만으로 metric을 고르지 말 것.
- 회사의 사업 구조, sector, industry, description과 함께 해석할 것.
- 최신 뉴스, 계약, 공시, 실적 이벤트, M&A, 임원 거래를 생성하지 말 것.
- key_metrics의 metric은 위 '선택 가능 목록' 안에서만 고를 것.
- 출력 JSON 스키마는 아래 형식과 동일하게 유지할 것.

[출력] JSON만. 한국어.
{{
  "competitors": [
    {{"business_area": "영역명", "tickers": ["T1","T2"], "descriptions": ["T1 경쟁 이유","T2 경쟁 이유"]}}
  ],
  "key_metrics": [
    {{"metric": "지표ID", "reason": "선택 이유"}}
  ]
}}
"""


async def get_stock_profile(ticker: str, overview: dict = None) -> dict:
    """종목별 AI 프로필 (경쟁사 + 핵심지표) 반환. DB 캐시 우선."""
    ticker = ticker.upper()

    # 1) DB 캐시 확인
    cached = await _get_cached_profile(ticker)
    if cached:
        return cached

    # 2) overview 없으면 가져오기
    if not overview:
        from app.services import yfinance_client
        overview = await asyncio.to_thread(yfinance_client.get_overview, ticker)

    # 3) Gemini 분석
    profile = await _analyze_with_gemini(ticker, overview)
    if profile:
        await _save_profile(ticker, profile)

    return profile or {"competitors": [], "key_metrics": []}


async def _get_cached_profile(ticker: str) -> dict | None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM stock_profile_ai WHERE ticker = ?",
                (ticker,)
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "competitors": json.loads(row["competitors_json"]),
                    "key_metrics": json.loads(row["key_metrics_json"]),
                    "cached": True,
                }
    except Exception as e:
        logger.warning("stock_profile_ai cache read error: %s", e)
    return None


async def _save_profile(ticker: str, profile: dict):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO stock_profile_ai
                (ticker, competitors_json, key_metrics_json, analyzed_at)
                VALUES (?, ?, ?, ?)
            """, (
                ticker,
                json.dumps(profile["competitors"], ensure_ascii=False),
                json.dumps(profile["key_metrics"], ensure_ascii=False),
                datetime.now().isoformat(),
            ))
            await db.commit()
    except Exception as e:
        logger.warning("stock_profile_ai save error: %s", e)


def _build_financial_context(overview: dict) -> str:
    """key metric 선택 참고용 compact 재무 컨텍스트 문자열 생성.

    overview의 5개 필드만 사용 (revenue_growth, gross_margin, operating_margin,
    roe, debt_to_equity). 외부 API/DB/frontend 참조 없음.
    값이 None이면 'N/A'로 표기하되 라인 자체는 유지한다.
    margin/growth/roe는 overview에 비율(소수)로 저장되므로 ×100하여 % 표기,
    debt_to_equity는 배수(ratio)로 그대로 표기.
    """
    def _pct(v) -> str:
        return f"{v * 100:.1f}%" if v is not None else "N/A"

    def _ratio(v) -> str:
        return f"{v:.2f}" if v is not None else "N/A"

    lines = [
        "Financial context (참고용):",
        f"- revenue_growth: {_pct(overview.get('revenue_growth'))}",
        f"- gross_margin: {_pct(overview.get('gross_margin'))}",
        f"- operating_margin: {_pct(overview.get('operating_margin'))}",
        f"- roe: {_pct(overview.get('roe'))}",
        f"- debt_to_equity: {_ratio(overview.get('debt_to_equity'))}",
    ]
    return "\n".join(lines)


def _extract_profile_inputs(ticker: str, overview: dict) -> dict:
    """PROFILE_PROMPT.format()에 넣을 입력값 추출 (동작 보존 헬퍼).

    반환 dict의 key는 PROFILE_PROMPT placeholder와 동일하게 유지한다.
    market_cap은 raw 숫자가 아니라 기존 로직으로 포맷된 문자열이다.
    financial_context는 _build_financial_context로 만든 참고용 문자열이다.
    """
    company_name = overview.get("name", ticker)
    sector = overview.get("sector", "Unknown")
    industry = overview.get("industry", "Unknown")
    market_cap = overview.get("market_cap", 0)
    description = overview.get("description", "")[:500]

    # 시가총액 포맷
    if market_cap and market_cap > 1e12:
        mc_str = f"${market_cap/1e12:.1f}T"
    elif market_cap and market_cap > 1e9:
        mc_str = f"${market_cap/1e9:.1f}B"
    else:
        mc_str = f"${market_cap:,.0f}" if market_cap else "N/A"

    return {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "market_cap": mc_str,
        "description": description,
        "financial_context": _build_financial_context(overview),
    }


async def _analyze_with_gemini(ticker: str, overview: dict) -> dict | None:
    if not GOOGLE_API_KEY:
        logger.warning("GOOGLE_API_KEY not set")
        return None

    inputs = _extract_profile_inputs(ticker, overview)
    prompt = PROFILE_PROMPT.format(**inputs)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2000,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = await asyncio.to_thread(
            lambda: requests.post(url, json=payload, timeout=30)
        )
        if resp.status_code != 200:
            logger.error("Gemini API error %s: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)

        # 유효성 검증
        competitors = result.get("competitors", [])
        key_metrics = result.get("key_metrics", [])

        if not isinstance(competitors, list) or not isinstance(key_metrics, list):
            logger.error("Invalid Gemini response format")
            return None

        return {
            "competitors": competitors,
            "key_metrics": key_metrics,
        }

    except Exception as e:
        logger.error("Gemini stock profile error: %s", e)
        return None
