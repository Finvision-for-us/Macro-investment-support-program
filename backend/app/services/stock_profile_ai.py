"""Gemini AI 기반 종목별 프로필 분석 서비스

각 종목에 대해:
1. 사업 영역별 실제 경쟁사 선정
2. 해당 종목에서 중요한 핵심 지표 선정

결과는 DB에 영구 캐싱 (한 번 분석하면 재분석 안 함).
"""

import aiosqlite
import asyncio
import hashlib
import json
import logging
import re
import requests
from datetime import datetime

from app.config import GOOGLE_API_KEY
from app.database import DB_PATH

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash-lite"

# profile 생성 설정의 의미적 버전. context/output schema 의미가 바뀔 때만 +1.
# (함수 코드 자체는 hash에 넣지 않는다 — 무해한 refactor로 cache 전체가 무효화되는 것을 피하기 위함.)
PROFILE_CONTEXT_VERSION = 2

PROFILE_PROMPT = """[역할] 종목 프로파일링 전문가. 사업 구조를 분석하여 직접 경쟁사를 식별하고, 해당 종목에 최적화된 분석 지표를 선정.

[대상] {ticker} ({company_name}) | {sector} > {industry} | 시총 {market_cap}
[사업] {description}

[재무 컨텍스트 — key metric 선택 참고용 보조 정보]
{financial_context}

[Task 1: 경쟁사] 사업 영역별 '직접' 경쟁사 선정

[직접 경쟁사 정의]
- 직접 경쟁사 = 대상 기업과 같은 제품/서비스 범주에서 '동일 고객의 구매 결정'을 두고 지금 실제로 다투는 회사.
- 아래는 직접 경쟁사가 아니므로 넣지 말 것:
  · 대상 기업이 그 회사의 제품/서비스를 '사서 쓰는 고객'인 경우
    (예: Apple은 AWS/Azure/GCP의 고객이므로 '클라우드 인프라'는 Apple의 경쟁 영역이 아니다)
  · 해당 사업에서 철수했거나 실질 점유가 없는 회사
    (예: 스마트폰에서 철수한 Microsoft는 Apple '스마트폰' 경쟁사가 아니다)
  · 공급업체·협력사·단순 동종 산업군

[선정 규칙]
- 각 영역마다 '지금 실제로 겹치는 구체적 제품 라인'을 댈 수 있는 회사만 넣을 것.
  구체적 제품 라인을 명확히 댈 수 없으면 넣지 말 것.
- 개수 채우기 금지: 진짜 직접 경쟁사가 부족하면 영역을 비우거나 수를 줄일 것.
  약한 3개보다 확실한 2개가 낫다. 총 개수를 억지로 채우지 말 것.
- 대상 기업이 그 영역에서 실제 사업을 하지 않으면 그 영역 자체를 만들지 말 것.

[ticker 규칙]
- tickers에는 '미국에서 실제 조회 가능한' ticker symbol만 넣을 것 (NYSE/NASDAQ + ADR/OTC 허용).
- 회사명(예: "SAMSUNG")을 ticker 자리에 쓰지 말 것. ticker를 추측/변형하지 말 것.
- 아래 주요 해외 기업은 반드시 이 미국 거래 ticker를 그대로 사용할 것 (다른 표기 금지):
  삼성전자=SSNLF, 샤오미=XIACY, 레노버=LNVGY
- 위 목록에 없는 해외 기업은, 정확한 미국 거래 ticker를 확신할 수 없으면 생략할 것 (ticker 추측 금지).
- 비상장 제외. 영역당 최대 3개.

[각 경쟁사 self-check]
- 넣기 전에 스스로 확인: "이 회사는 이 영역에서 대상 기업과 지금 실제로 경쟁하는가? 구체적 제품이 있는가?" 아니면 제외.

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


def _compute_profile_hash() -> str:
    """현재 profile 생성 설정의 sha256 hash (full 64 hex).

    입력: PROFILE_PROMPT(텍스트) + GEMINI_MODEL + PROFILE_CONTEXT_VERSION.
    prompt/model/context-version이 바뀌면 hash가 바뀐다. 함수 코드 자체는 포함하지 않는다.
    """
    payload = json.dumps(
        {
            "prompt": PROFILE_PROMPT,
            "model": GEMINI_MODEL,
            "context_version": PROFILE_CONTEXT_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 모듈 로드 시 1회 계산 (PROFILE_PROMPT 정의 이후여야 함)
CURRENT_PROFILE_HASH = _compute_profile_hash()

# competitor 저장 가드 기준 (fail-closed): 정제 후 이 기준 미달이면 새 profile을 저장하지 않는다.
_MIN_COMPETITOR_GROUPS = 1
_MIN_VALID_COMPETITOR_TICKERS = 2
# ticker 형식 1차 필터. 형식만으로는 'SAMSUNG' 같은 회사명을 못 거른다(실재성은 validator가 담당).
_TICKER_FORMAT_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# 비평(critic) 에이전트 프롬프트. 1차 생성 결과의 '판단'(직접성/주력영역 배치)만 재검토한다.
# 티커는 바꾸지 않고(추측 금지), 회사 추가도 하지 않으며, 제거·재배치만 한다.
_CRITIC_PROMPT = """[역할] 경쟁사 분석 검수자. 다른 분석가가 작성한 '{ticker} ({company_name})의 경쟁사 분류'를 비판적으로 재검토하고, 틀린 부분만 고친다.

[대상] {ticker} ({company_name}) | {sector} > {industry}
[사업] {description}

[검토 대상 경쟁사 분류(JSON)]
{competitors_json}

[검토 기준]
- 영역(business_area)은 대상 기업의 실제 사업 구분을 따르며, 제품군·서비스·고객군·사업부문 등 무엇이든 될 수 있다.
- 각 회사가 대상 기업의 '직접' 경쟁사인가 (같은 제품/서비스/고객을 두고 실제로 경쟁). 아니면 제거.
- 대상 기업이 그 회사의 '고객'이면 경쟁사가 아니다 → 제거.
- 해당 사업에서 철수했거나 실질 점유가 없으면 → 제거.
- 각 회사가 대상 기업과 '가장 크게 경쟁하는 주력 영역'에 배치됐는가.
  한 회사가 특정 영역에서 가장 크게 경쟁하면 그 영역에 둘 것.
  단, 여러 영역에서 대등하게 경쟁하는 경우(예: 종합 금융사끼리)에는 억지로 한 곳으로 몰지 말 것.
- 틀린 게 없으면 원본을 그대로 유지할 것 (억지로 바꾸지 말 것).

[출력 규칙]
- ticker는 원본에 있던 값만 사용할 것. 새 ticker를 만들거나 추측하지 말 것.
- 원본에 없던 회사를 새로 추가하지 말 것 (제거·재배치만).
- 아래와 완전히 동일한 JSON 스키마로만 출력. 한국어. JSON 외 텍스트 금지.
{{
  "competitors": [
    {{"business_area": "영역명", "tickers": ["T1"], "descriptions": ["T1 경쟁 이유"]}}
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
        # 3.5) 비평 에이전트 (판단층, fail-soft): 직접성/주력영역을 2차 Gemini로 재검토.
        #      실패(503/파싱오류 등)면 None → 원본 competitors 유지. 티커 사실검증은 아래 단계가 담당.
        critiqued = await _critique_competitors(ticker, overview, profile.get("competitors", []))
        if critiqued is not None:
            profile["competitors"] = critiqued

        # competitor 정제 (저장 전, fail-closed). raw model output을 그대로 저장하지 않는다.
        #   (1) 구조 정제: 형식/중복/self/정렬 — 순수 함수, 네트워크 없음
        #   (2) 실재성 병렬 검증: 후보 ticker를 get_overview로 병렬 조회 (stock.py peer 조회와 동일 패턴)
        #   (3) valid 집합으로 필터: 조회 불가 ticker / 빈 group 제거
        structural = _sanitize_competitors(ticker, profile.get("competitors", []))
        candidate_tickers = {t for g in structural for t in g["tickers"]}
        valid_tickers = await _validate_competitor_tickers(candidate_tickers)
        sanitized = _filter_competitors_by_valid(structural, valid_tickers)
        profile["competitors"] = sanitized

        # 저장 가드: valid competitor가 기준 미달이면 새 profile을 저장하지 않는다.
        # (cache miss 경로이므로 덮어쓸 기존 cache는 없다. 저장만 보류하고 결과는 그대로 반환한다.)
        valid_ticker_count = sum(len(g["tickers"]) for g in sanitized)
        if len(sanitized) >= _MIN_COMPETITOR_GROUPS and valid_ticker_count >= _MIN_VALID_COMPETITOR_TICKERS:
            await _save_profile(ticker, profile)
        else:
            logger.info(
                "stock_profile_ai save skipped (insufficient valid competitors): "
                "ticker=%s groups=%d valid_tickers=%d",
                ticker, len(sanitized), valid_ticker_count,
            )

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
                # profile_hash 컬럼이 없는 옛 DB에서도 안전하게 처리 (방어적)
                row_keys = row.keys()
                row_hash = row["profile_hash"] if "profile_hash" in row_keys else None
                analyzed_at = row["analyzed_at"] if "analyzed_at" in row_keys else None
                # NULL이거나 현재 hash와 다르면 old/stale cache. stale이어도 자동 재호출하지 않는다.
                stale = (row_hash is None) or (row_hash != CURRENT_PROFILE_HASH)
                if stale:
                    logger.info(
                        "stock_profile_ai stale cache: ticker=%s row_hash=%s current=%s",
                        ticker, row_hash, CURRENT_PROFILE_HASH,
                    )
                return {
                    "competitors": json.loads(row["competitors_json"]),
                    "key_metrics": json.loads(row["key_metrics_json"]),
                    "cached": True,
                    "stale": stale,
                    "profile_hash": row_hash,
                    "analyzed_at": analyzed_at,
                }
    except Exception as e:
        logger.warning("stock_profile_ai cache read error: %s", e)
    return None


async def _save_profile(ticker: str, profile: dict):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO stock_profile_ai
                (ticker, competitors_json, key_metrics_json, analyzed_at, profile_hash)
                VALUES (?, ?, ?, ?, ?)
            """, (
                ticker,
                json.dumps(profile["competitors"], ensure_ascii=False),
                json.dumps(profile["key_metrics"], ensure_ascii=False),
                datetime.now().isoformat(),
                CURRENT_PROFILE_HASH,
            ))
            await db.commit()
    except Exception as e:
        logger.warning("stock_profile_ai save error: %s", e)


def _sanitize_competitors(ticker, competitors):
    """경쟁사 목록을 '구조' 기준으로만 정제한다 (순수 함수, 네트워크 호출 없음).

    - non-list 입력 / non-dict group 방어
    - business_area 없으면 group 제거, tickers가 list 아니면 group 제거
    - ticker trim/uppercase, 자기 ticker 제거, 중복 제거
    - 형식 1차 필터(_TICKER_FORMAT_RE)
    - descriptions를 ticker와 정렬 유지하여 보정
    - ticker가 0개가 된 group 제거

    형식 필터만으로는 'SAMSUNG' 같은 회사명을 못 거른다. 실재성 검증은
    _validate_competitor_tickers + _filter_competitors_by_valid가 담당한다.
    """
    if not isinstance(competitors, list):
        return []
    self_upper = (ticker or "").strip().upper()
    cleaned = []
    for group in competitors:
        if not isinstance(group, dict):
            continue
        area = group.get("business_area")
        if not isinstance(area, str) or not area.strip():
            continue
        tickers = group.get("tickers")
        if not isinstance(tickers, list):
            continue
        descriptions = group.get("descriptions")
        if not isinstance(descriptions, list):
            descriptions = []

        seen = set()
        kept_tickers = []
        kept_descs = []
        for i, t in enumerate(tickers):
            if not isinstance(t, str):
                continue
            sym = t.strip().upper()
            if not sym or sym == self_upper or sym in seen:
                continue
            if not _TICKER_FORMAT_RE.match(sym):
                continue
            seen.add(sym)
            kept_tickers.append(sym)
            desc = descriptions[i] if i < len(descriptions) and isinstance(descriptions[i], str) else ""
            kept_descs.append(desc)

        if kept_tickers:
            cleaned.append({
                "business_area": area,
                "tickers": kept_tickers,
                "descriptions": kept_descs,
            })
    return cleaned


def _filter_competitors_by_valid(groups, valid_tickers):
    """구조 정제된 groups에서 valid_tickers 집합에 없는 ticker를 제거한다 (순수 함수).

    descriptions 정렬을 유지하고, ticker가 0개가 된 group은 제거한다.
    입력 groups는 이미 _sanitize_competitors를 통과한(대문자/중복제거/정렬된) 형태로 가정한다.
    """
    out = []
    for group in groups:
        tickers = group.get("tickers", [])
        descriptions = group.get("descriptions", [])
        kept_t = []
        kept_d = []
        for i, t in enumerate(tickers):
            if t in valid_tickers:
                kept_t.append(t)
                kept_d.append(descriptions[i] if i < len(descriptions) else "")
        if kept_t:
            out.append({
                "business_area": group.get("business_area", ""),
                "tickers": kept_t,
                "descriptions": kept_d,
            })
    return out


def _is_valid_competitor_ticker(symbol) -> bool:
    """단일 ticker가 실제 조회 가능한 종목인지 검증한다 (fail-closed).

    기존 yfinance_client.get_overview를 재사용한다. quote-like 핵심 필드
    (market_cap 또는 current_price)가 존재하면 valid로 본다.
    주의: get_overview는 exchange를 반환하지 않으므로 NYSE/NASDAQ 상장 여부까지는
    검증하지 못한다. '실재/조회 가능 ticker' 검증까지만 보장한다.
    예외 발생 또는 빈 데이터면 invalid (fail-closed).
    """
    try:
        from app.services import yfinance_client
        ov = yfinance_client.get_overview(symbol)
        if not isinstance(ov, dict):
            return False
        return ov.get("market_cap") is not None or ov.get("current_price") is not None
    except Exception as e:
        logger.warning("competitor ticker validation error: symbol=%s err=%s", symbol, e)
        return False


async def _validate_competitor_tickers(tickers) -> set:
    """후보 ticker들의 실재성을 병렬로 검증해 valid한 ticker 집합을 반환한다.

    stock.py의 peer 조회와 동일하게 get_overview를 병렬(asyncio.gather)로 호출한다.
    각 get_overview는 동기 + 네트워크 I/O이므로 asyncio.to_thread로 감싼다.
    네트워크 검증이 cache miss + Gemini 성공 경로에서만 발생한다(드묾).
    """
    symbols = list(tickers)
    if not symbols:
        return set()

    async def _check(sym):
        ok = await asyncio.to_thread(_is_valid_competitor_ticker, sym)
        return sym if ok else None

    results = await asyncio.gather(*[_check(s) for s in symbols])
    return {s for s in results if s}


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


async def _critique_competitors(ticker, overview, competitors):
    """2차 Gemini 비평 패스: 경쟁사 직접성/주력영역만 재검토한다 (판단층, fail-soft).

    티커는 바꾸지 않고(추측 금지), 회사 추가 없이 제거·재배치만 한다.
    실패(키 없음/빈 입력/HTTP 오류/파싱 실패)면 None을 반환하고, 호출측은 원본 competitors를 유지한다.
    사실(티커 존재) 검증은 호출측의 _validate_competitor_tickers가 담당한다.
    """
    if not GOOGLE_API_KEY:
        return None
    if not isinstance(competitors, list) or not competitors:
        return None

    inputs = _extract_profile_inputs(ticker, overview)
    try:
        prompt = _CRITIC_PROMPT.format(
            ticker=ticker,
            company_name=inputs["company_name"],
            sector=inputs["sector"],
            industry=inputs["industry"],
            description=inputs["description"],
            competitors_json=json.dumps(competitors, ensure_ascii=False),
        )
    except Exception as e:
        logger.warning("critic prompt format error: %s", e)
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2000,
            "responseMimeType": "application/json",
        },
    }
    try:
        resp = await asyncio.to_thread(lambda: requests.post(url, json=payload, timeout=30))
        if resp.status_code != 200:
            logger.error("critic Gemini error %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        comps = result.get("competitors")
        if not isinstance(comps, list) or not comps:
            return None
        return comps
    except Exception as e:
        logger.error("critic error: %s", e)
        return None


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
