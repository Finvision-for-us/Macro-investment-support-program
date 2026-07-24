from fastapi import APIRouter, HTTPException
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.services import yfinance_client
from app.services.stock_dictionary import search_suggest
from app.services.sec_client import get_filings
from app.services.news_client import get_stock_news

router = APIRouter(prefix="/api/stock", tags=["stock"])

_chart_pool = ThreadPoolExecutor(max_workers=6)


def _enrich_with_chart(item: dict) -> dict:
    ticker = item["ticker"]
    try:
        chart = yfinance_client._yf_chart(ticker, interval="1d", range_="5d", timeout=5)
        if chart:
            chart_result = chart.get("chart", {}).get("result", [])
            if chart_result:
                meta = chart_result[0].get("meta", {})
                closes = chart_result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                valid_closes = [c for c in closes if c is not None]
                price = meta.get("regularMarketPrice")
                prev_close = meta.get("chartPreviousClose") or (valid_closes[0] if valid_closes else None)
                change_pct = None
                if price and prev_close and prev_close != 0:
                    change_pct = round((price - prev_close) / prev_close * 100, 2)
                sparkline = [round(c, 2) for c in valid_closes[-5:]] if valid_closes else []
                item["price"] = round(price, 2) if price else None
                item["change_pct"] = change_pct
                item["sparkline"] = sparkline
                return item
    except Exception:
        pass
    item["price"] = None
    item["change_pct"] = None
    item["sparkline"] = []
    return item


@router.get("/search")
def search(q: str):
    results = yfinance_client.search_ticker(q)
    return {"results": results}


@router.get("/suggest")
def suggest(q: str, enrich: int = 0):
    """검색 제안. enrich=0이면 즉시 반환, enrich=1이면 차트 데이터 포함"""
    if not q or not q.strip():
        return {"results": []}

    query = q.strip()

    # 1단계: 통합 로컬 검색 — 큐레이트 사전 + SEC 전체 유니버스(~10,400) +
    # 한글 로마자 브리지. 매치 품질(티어)로 정렬돼 정확/접두가 위로.
    merged = [
        {"ticker": r["ticker"], "name": r["name"], "exchange": "", "sector": ""}
        for r in search_suggest(query, max_results=8)
    ]

    # 2단계: Yahoo 최종 보충 — 로컬 결과가 부족할 때만(신규 상장·해외 등)
    if len(merged) < 3:
        seen = {m["ticker"] for m in merged}
        try:
            for item in yfinance_client.search_ticker(query):
                t = item["ticker"]
                if "." in t or t in seen:  # 외국 거래소·중복 제외
                    continue
                seen.add(t)
                merged.append(item)
        except Exception:
            pass

    # 상위 5개만
    merged = merged[:5]

    if not merged:
        return {"results": []}

    # enrich=0 → 차트 없이 즉시 반환 (드롭다운 빠른 표시용)
    if not enrich:
        for item in merged:
            item.setdefault("price", None)
            item.setdefault("change_pct", None)
            item.setdefault("sparkline", [])
        return {"results": merged}

    # enrich=1 → 차트 데이터 병렬 요청 (상위 4개만)
    to_enrich = merged[:4]
    no_enrich = merged[4:]

    futures = {_chart_pool.submit(_enrich_with_chart, item): item for item in to_enrich}
    enriched = []
    for future in as_completed(futures):
        try:
            enriched.append(future.result())
        except Exception:
            enriched.append(futures[future])

    for item in no_enrich:
        item["price"] = None
        item["change_pct"] = None
        item["sparkline"] = []
        enriched.append(item)

    order = {item["ticker"]: i for i, item in enumerate(merged)}
    enriched.sort(key=lambda x: order.get(x["ticker"], 99))

    return {"results": enriched}


@router.get("/{ticker}/overview")
def overview(ticker: str):
    try:
        return yfinance_client.get_overview(ticker)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/{ticker}/quote")
def quote(ticker: str):
    """실시간 가격만 경량으로 반환 (30초 자동 갱신용)"""
    try:
        chart = yfinance_client._yf_chart(ticker, interval="1d", range_="1d")
        if not chart:
            raise HTTPException(status_code=404, detail="No data")
        result = chart.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})
        return {
            "ticker": ticker.upper(),
            "current_price": meta.get("regularMarketPrice"),
            "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "market_time": meta.get("regularMarketTime"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{ticker}/price")
def price(ticker: str, period: str = "1y"):
    valid_periods = ["1d", "3d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "10y", "max"]
    if period not in valid_periods:
        period = "1y"
    data = yfinance_client.get_price_history(ticker, period)
    return {"ticker": ticker, "period": period, "data": data}

@router.get("/{ticker}/financials")
def financials(ticker: str):
    return yfinance_client.get_financials(ticker)

@router.get("/{ticker}/metric-history")
def metric_history(ticker: str):
    """주요 재무 지표의 연도별 히스토리 반환 (차트용)"""
    try:
        return yfinance_client.get_metric_history(ticker)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{ticker}/filings")
def filings(ticker: str):
    data = get_filings(ticker)
    return {"ticker": ticker, "filings": data}

@router.get("/{ticker}/news")
def news(ticker: str):
    from app.services.stock_dictionary import STOCK_DB
    company_name = ticker.upper()
    for t, name, kr_names in STOCK_DB:
        if t == ticker.upper():
            company_name = name
            break
    return get_stock_news(ticker, company_name, limit=15)

@router.get("/{ticker}/competitors")
async def get_competitors(ticker: str):
    """AI 기반 사업 영역별 실제 경쟁사 비교 데이터 반환"""
    import asyncio
    from app.services.stock_profile_ai import get_stock_profile

    # 1) 해당 종목의 개요
    overview = await asyncio.to_thread(yfinance_client.get_overview, ticker)
    industry = overview.get("industry", "")
    sector = overview.get("sector", "")

    # 2) AI 프로필 (경쟁사 + 핵심지표) — DB 캐시 우선
    profile = await get_stock_profile(ticker, overview)
    ai_competitors = profile.get("competitors", [])

    # 3) 모든 경쟁사 티커 수집
    all_peer_tickers = []
    for group in ai_competitors:
        for t in group.get("tickers", []):
            if t.upper() != ticker.upper() and t not in all_peer_tickers:
                all_peer_tickers.append(t)

    # 4) 각 경쟁사 지표 비동기 조회
    async def fetch_peer(t):
        try:
            ov = await asyncio.to_thread(yfinance_client.get_overview, t)
            return {
                "ticker": t,
                "name": ov.get("name", t),
                "market_cap": ov.get("market_cap"),
                "pe_ratio": ov.get("pe_ratio"),
                "forward_pe": ov.get("forward_pe"),
                "profit_margin": ov.get("profit_margin"),
                "revenue_growth": ov.get("revenue_growth"),
                "roe": ov.get("roe"),
                "debt_to_equity": ov.get("debt_to_equity"),
                "current_price": ov.get("current_price"),
            }
        except Exception:
            return None

    results = await asyncio.gather(*[fetch_peer(p) for p in all_peer_tickers])
    peer_data = {r["ticker"]: r for r in results if r}

    # 5) 사업 영역별 그룹화
    competitor_groups = []
    for group in ai_competitors:
        area = group.get("business_area", "")
        tickers = group.get("tickers", [])
        descriptions = group.get("descriptions", [])
        peers = []
        for i, t in enumerate(tickers):
            data = peer_data.get(t)
            if data:
                data["description"] = descriptions[i] if i < len(descriptions) else ""
                peers.append(data)
        if peers:
            competitor_groups.append({
                "business_area": area,
                "peers": peers,
            })

    # 6) 원본 종목
    main_stock = {
        "ticker": ticker.upper(),
        "name": overview.get("name", ticker),
        "market_cap": overview.get("market_cap"),
        "pe_ratio": overview.get("pe_ratio"),
        "forward_pe": overview.get("forward_pe"),
        "profit_margin": overview.get("profit_margin"),
        "revenue_growth": overview.get("revenue_growth"),
        "roe": overview.get("roe"),
        "debt_to_equity": overview.get("debt_to_equity"),
        "current_price": overview.get("current_price"),
    }

    return {
        "ticker": ticker.upper(),
        "sector": sector,
        "industry": industry,
        "main": main_stock,
        "competitor_groups": competitor_groups,
        "key_metrics": profile.get("key_metrics", []),
    }


@router.get("/{ticker}/earnings-calendar")
async def get_earnings_calendar(ticker: str):
    """메인 종목 + 동종 업계 실적 발표 일정 캘린더"""
    import asyncio
    import requests as _requests

    overview = await asyncio.to_thread(yfinance_client.get_overview, ticker)
    earnings_date = overview.get("earnings_date")

    # 동종 업계 종목 가져오기
    peers: list[str] = []
    try:
        url = f"https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{ticker}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = _requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        for rec in data.get("finance", {}).get("result", [{}])[0].get("recommendedSymbols", []):
            sym = rec.get("symbol", "")
            if sym and "." not in sym:
                peers.append(sym)
        peers = peers[:5]
    except Exception:
        pass

    calendar = []

    # 메인 종목
    if earnings_date:
        calendar.append({
            "ticker": ticker.upper(),
            "name": overview.get("name", ticker),
            "earnings_date": earnings_date,
            "is_main": True,
        })

    # 동종 업계 실적 일정 병렬 조회
    async def fetch_peer_date(t: str):
        try:
            ov = await asyncio.to_thread(yfinance_client.get_overview, t)
            ed = ov.get("earnings_date")
            if ed:
                return {"ticker": t, "name": ov.get("name", t), "earnings_date": ed, "is_main": False}
        except Exception:
            pass
        return None

    if peers:
        results = await asyncio.gather(*[fetch_peer_date(p) for p in peers])
        for r in results:
            if r:
                calendar.append(r)

    # 날짜순 정렬
    calendar.sort(key=lambda x: x["earnings_date"] or "9999")

    return {"ticker": ticker.upper(), "calendar": calendar}


@router.get("/{ticker}/analyst-vs-ai")
async def get_analyst_vs_ai(ticker: str):
    """애널리스트 컨센서스 vs AI 가이던스 분석 비교"""
    import asyncio
    import json
    import aiosqlite
    from app.database import DB_PATH

    # Get analyst data from Yahoo Finance
    overview = await asyncio.to_thread(yfinance_client.get_overview, ticker)

    analyst = {
        "target_mean": overview.get("target_mean"),
        "target_high": overview.get("target_high"),
        "target_low": overview.get("target_low"),
        "recommendation": overview.get("recommendation"),
        "current_price": overview.get("current_price"),
        "forward_pe": overview.get("forward_pe"),
        "eps": overview.get("eps"),
    }

    # Calculate analyst implied upside
    if analyst["target_mean"] and analyst["current_price"]:
        analyst["implied_upside"] = round(
            (analyst["target_mean"] - analyst["current_price"]) / analyst["current_price"] * 100, 1
        )
    else:
        analyst["implied_upside"] = None

    # Get AI guidance data from guidance_analysis table
    ai_analysis = None
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM guidance_analysis WHERE ticker = ? ORDER BY period_end DESC LIMIT 4",
                (ticker.upper(),)
            )
            rows = await cursor.fetchall()

            if rows:
                recent = rows[0]
                themes = json.loads(recent["key_themes"]) if recent["key_themes"] else []

                # Calculate AI sentiment trend from recent quarters
                sentiments = []
                for r in rows:
                    s = r["sentiment_score"]
                    if s is not None:
                        sentiments.append(s)

                avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 50
                trend = "positive" if avg_sentiment > 55 else "negative" if avg_sentiment < 45 else "neutral"

                ai_analysis = {
                    "latest_sentiment": recent["sentiment_score"],
                    "avg_sentiment": round(avg_sentiment, 1),
                    "trend": trend,
                    "quarters_analyzed": len(rows),
                    "latest_themes": themes[:5] if isinstance(themes, list) else [],
                    "guidance_summary": recent["guidance_summary"],
                    "revenue_guidance": recent["revenue_guidance"],
                    "margin_guidance": recent["margin_guidance"],
                    "latest_period": recent["period_end"],
                }
    except Exception:
        pass

    return {
        "ticker": ticker.upper(),
        "analyst": analyst,
        "ai": ai_analysis,
    }


@router.post("/translate")
async def translate_text(body: dict):
    """Gemini로 자연스러운 한국어 번역 (캐시)"""
    import asyncio
    import aiosqlite
    import hashlib
    from app.database import DB_PATH

    text = body.get("text", "").strip()
    if not text:
        return {"translated": ""}

    text_hash = hashlib.md5(text.encode()).hexdigest()

    # 캐시 확인
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS translation_cache (
                text_hash TEXT PRIMARY KEY,
                original TEXT,
                translated TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = await db.execute("SELECT translated FROM translation_cache WHERE text_hash = ?", (text_hash,))
        cached = await row.fetchone()
        if cached:
            return {"translated": cached[0]}

    # Gemini 번역
    import google.generativeai as genai
    import os
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-flash-lite")

    prompt = f"""다음 영문 기업 소개를 한국인 투자자가 읽기 편한 자연스러운 한국어로 번역해줘.
직역이 아닌 의역으로, 한국 금융 용어를 사용해서 매끄럽게 작성해.
원문의 핵심 정보는 빠뜨리지 말고, 불필요한 수식어는 줄여서 간결하게.
번역문만 출력해. 다른 설명 없이.

{text}"""

    response = await asyncio.to_thread(model.generate_content, prompt)
    translated = response.text.strip()

    # 캐시 저장
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO translation_cache (text_hash, original, translated) VALUES (?, ?, ?)",
            (text_hash, text, translated)
        )
        await db.commit()

    return {"translated": translated}
