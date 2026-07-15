import re
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Simple TTL cache for overview data (5 min TTL)
_overview_cache = {}
_OVERVIEW_TTL = 300  # 5 minutes

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── 섹터별 핵심 지표 (해당 섹터에서 가장 중요하게 봐야 할 지표) ────────
SECTOR_KEY_METRICS = {
    "Technology":              ["pe_ratio", "revenue_growth", "profit_margin", "roe", "fcf", "roic", "ocf_margin"],
    "Healthcare":              ["pe_ratio", "revenue_growth", "operating_margin", "debt_to_equity", "fcf", "roic"],
    "Financial Services":      ["pb_ratio", "roe", "dividend_yield", "debt_to_equity", "profit_margin", "asset_turnover"],
    "Consumer Cyclical":       ["pe_ratio", "revenue_growth", "roe", "current_ratio", "operating_margin", "inventory_turnover", "asset_turnover"],
    "Consumer Defensive":      ["dividend_yield", "pe_ratio", "profit_margin", "debt_to_equity", "current_ratio", "payout_ratio", "inventory_turnover"],
    "Energy":                  ["dividend_yield", "fcf", "debt_to_equity", "operating_margin", "pe_ratio", "capex_to_revenue", "ocf_margin"],
    "Industrials":             ["pe_ratio", "roe", "debt_to_equity", "operating_margin", "revenue_growth", "asset_turnover", "roic"],
    "Communication Services":  ["pe_ratio", "revenue_growth", "profit_margin", "fcf", "roe", "ocf_margin"],
    "Utilities":               ["dividend_yield", "debt_to_equity", "pe_ratio", "operating_margin", "current_ratio", "payout_ratio"],
    "Real Estate":             ["dividend_yield", "pb_ratio", "debt_to_equity", "fcf", "operating_margin", "payout_ratio"],
    "Basic Materials":         ["pe_ratio", "pb_ratio", "dividend_yield", "debt_to_equity", "roe", "inventory_turnover"],
}


# ── Yahoo Finance Crumb Session (v10 API 인증용) ──────────────────
_session = None
_crumb = None
_crumb_lock = threading.Lock()
_crumb_expires = 0  # timestamp

def _ensure_crumb():
    global _session, _crumb, _crumb_expires
    with _crumb_lock:
        now = time.time()
        if _crumb and _session and now < _crumb_expires:
            return _session, _crumb
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": HEADERS["User-Agent"],
            })
            s.get("https://fc.yahoo.com", timeout=10, allow_redirects=True)
            r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
            r.raise_for_status()
            s.headers.update(HEADERS)
            _session = s
            _crumb = r.text
            _crumb_expires = now + 1800
            return _session, _crumb
        except Exception:
            return None, None


# ── 저수준 API 호출 ──────────────────────────────────────────────

def _yf_chart(ticker: str, interval: str = "1d", range_: str = "1y", timeout: int = 15):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
    params = {"interval": interval, "range": range_}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _yf_quoteSummary(ticker: str, modules: str = "assetProfile,defaultKeyStatistics,financialData,summaryDetail"):
    session, crumb = _ensure_crumb()
    if not session or not crumb:
        return {}
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker.upper()}"
    params = {"modules": modules, "crumb": crumb}
    try:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("quoteSummary", {}).get("result", [None])[0] or {}
    except Exception:
        return {}


def search_ticker(query: str):
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    params = {"q": query, "quotesCount": 6, "newsCount": 0, "listsCount": 0}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        results = []
        for q in data.get("quotes", []):
            if q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND"):
                results.append({
                    "ticker": q.get("symbol", ""),
                    "name": q.get("longname") or q.get("shortname", ""),
                    "exchange": q.get("exchDisp", ""),
                    "sector": q.get("sector", ""),
                    "quote_type": q.get("quoteType", ""),
                })
        return results
    except Exception:
        return []


# ── 이름 → 미국 거래 ticker 해석 ─────────────────────────────────
# Yahoo 검색 결과에서 '실제 회사(EQUITY)'이며 '미국에서 거래되는' ticker를 고른다.
# 회사별 하드코딩 없음. NYSE/NASDAQ 우선, 없으면 OTC. 펀드/ETF·이름 불일치는 제외.
_US_PRIMARY_EXCH = {
    "NYSE", "NASDAQ", "NasdaqGS", "NasdaqGM", "NasdaqCM",
    "NYSEArca", "NYSE American", "NYSEAmerican",
}
_FUND_WORDS = {"etf", "fund", "futures", "index", "etn", "leveraged"}
_NAME_STOP = {
    "the", "co", "ltd", "inc", "corp", "corporation", "company",
    "group", "holding", "holdings", "limited", "plc", "sa", "ag", "nv",
}


def _name_tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _name_match_ratio(query, cand_name):
    """질의 회사명의 핵심 토큰이 후보 회사명에 포함된 비율 (0~1). stopword 제외.

    완전일치 외에, 질의 토큰이 후보 토큰의 '접두'인 경우도 매칭으로 본다(4자 이상).
    예: 'Pepsi'(pepsi) 가 'PepsiCo'(pepsico)의 접두 → 매칭. (오탐 방지를 위해 접두만, 부분문자열 아님)
    """
    q = _name_tokens(query) - _NAME_STOP
    c = _name_tokens(cand_name) - _NAME_STOP
    if not q:
        return 0.0
    hit = 0
    for qt in q:
        if qt in c or any(len(qt) >= 4 and ct.startswith(qt) for ct in c):
            hit += 1
    return hit / len(q)


def _is_fund_name(name):
    return bool(_name_tokens(name) & _FUND_WORDS)


def _is_us_primary_exch(e):
    return e in _US_PRIMARY_EXCH


def _is_otc_exch(e):
    return "OTC" in (e or "") or e in ("Pink Sheets", "Other OTC")


def _select_us_ticker(query, quotes, min_name_match=0.5):
    """search_ticker 결과(quotes)에서 최적의 미국 거래 ticker 1개를 고른다 (순수 함수, network 없음).

    조건: quote_type == EQUITY, 펀드/ETF 이름 아님, 회사명 유사도 >= min_name_match.
    우선순위: 미국 정규거래소(NYSE/NASDAQ) > OTC. 같은 tier면 이름 유사도 높은 것.
    없으면 None.
    """
    if not isinstance(quotes, list):
        return None
    eq = [
        q for q in quotes
        if isinstance(q, dict)
        and q.get("quote_type") == "EQUITY"
        and not _is_fund_name(q.get("name", ""))
        and _name_match_ratio(query, q.get("name", "")) >= min_name_match
    ]
    t1 = [q for q in eq if _is_us_primary_exch(q.get("exchange", ""))]
    t2 = [q for q in eq if _is_otc_exch(q.get("exchange", ""))]
    tier = t1 or t2
    if not tier:
        return None
    tier.sort(key=lambda q: _name_match_ratio(query, q.get("name", "")), reverse=True)
    return tier[0].get("ticker") or None


def resolve_us_ticker(name):
    """회사 이름을 미국 거래 ticker로 해석한다 (Yahoo 검색 기반, 회사별 하드코딩 없음).

    못 찾거나 네트워크 실패면 None. 실제 선택 로직은 _select_us_ticker(순수 함수)가 담당한다.
    """
    if not name or not isinstance(name, str):
        return None
    return _select_us_ticker(name, search_ticker(name))


# ── 유틸리티 ─────────────────────────────────────────────────────

def _safe_raw(d: dict, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    if isinstance(cur, dict):
        return cur.get("raw")
    return cur


# ── 공개 API ─────────────────────────────────────────────────────

def get_overview(ticker: str):
    ticker_upper = ticker.upper()
    now = time.time()
    cached = _overview_cache.get(ticker_upper)
    if cached and now - cached["ts"] < _OVERVIEW_TTL:
        return cached["data"]

    # ts_fields must be defined before the parallel block
    ts_fields = [
        "annualNetIncome", "annualPretaxIncome", "annualEBIT",
        "annualInterestExpense", "annualIncomeTaxExpense",
        "annualAccountsReceivable", "annualInventory", "annualAccountsPayable",
        "annualTotalAssets", "annualCurrentAssets", "annualCurrentLiabilities",
        "annualTangibleBookValue", "annualOperatingIncome",
        "annualCapitalExpenditure", "annualCostOfRevenue", "annualTotalRevenue",
        "annualStockholdersEquity", "annualTotalDebt",
        "annualTotalLiabilitiesNetMinorityInterest",  # 부채비율(총부채/자기자본) 카드 계산용
        # 손익계산서 흐름 항목의 TTM 표시용(분기 4개 합). net_income(TTM)과 같은 그룹의
        # pretax_income·EBIT를 TTM으로 맞춰 '순이익 > 세전이익' 같은 기준혼용 오표시를 없앤다.
        # 필드 총 20개 — Yahoo timeseries 잘림 없음(실측 확인).
        "quarterlyPretaxIncome", "quarterlyEBIT",
    ]

    # Run all 3 API calls in parallel
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_chart = pool.submit(_yf_chart, ticker, "1d", "5d")
        f_summary = pool.submit(
            _yf_quoteSummary, ticker,
            "assetProfile,summaryDetail,defaultKeyStatistics,financialData,calendarEvents"
        )
        f_ts = pool.submit(_yf_timeseries, ticker, ts_fields)

        chart = f_chart.result()
        summary = f_summary.result()
        ts = f_ts.result()

    # v8 차트에서 가격/52주 데이터
    meta = {}
    if chart:
        results = chart.get("chart", {}).get("result", [])
        if results:
            meta = results[0].get("meta", {})

    # quoteSummary에서 핵심 재무 지표 + calendarEvents(실적 발표일)
    profile = summary.get("assetProfile", {})
    detail = summary.get("summaryDetail", {})
    stats = summary.get("defaultKeyStatistics", {})
    fin_data = summary.get("financialData", {})
    calendar = summary.get("calendarEvents", {})

    # 시가총액
    market_cap = _safe_raw(detail, "marketCap") or _safe_raw(stats, "marketCap")
    # PER
    pe_ratio = _safe_raw(detail, "trailingPE") or _safe_raw(stats, "trailingPE")
    # Forward PE
    forward_pe = _safe_raw(stats, "forwardPE") or _safe_raw(detail, "forwardPE")
    # EPS
    eps = _safe_raw(stats, "trailingEps")
    # PBR
    pb_ratio = _safe_raw(stats, "priceToBook")
    # 배당수익률
    dividend_yield = _safe_raw(detail, "dividendYield")
    # 설명
    description = profile.get("longBusinessSummary", "")
    # 섹터/산업/국가
    sector = profile.get("sector") or "-"
    industry = profile.get("industry") or "-"
    country = profile.get("country") or "-"
    # 목표가
    target_high = _safe_raw(fin_data, "targetHighPrice")
    target_low = _safe_raw(fin_data, "targetLowPrice")
    target_mean = _safe_raw(fin_data, "targetMeanPrice")
    # 성장성
    revenue_growth = _safe_raw(fin_data, "revenueGrowth")
    profit_margin = _safe_raw(fin_data, "profitMargins")
    current_price_fin = _safe_raw(fin_data, "currentPrice")

    # ── 추가 재무 지표 ──
    # ROE (Return on Equity)
    roe = _safe_raw(fin_data, "returnOnEquity")
    # ROA (Return on Assets)
    roa = _safe_raw(fin_data, "returnOnAssets")
    # 부채비율 (Debt-to-Equity) = 총부채(Liabilities) / 자기자본.
    # 값은 아래 timeseries 처리 후 계산한다(차트 debt_to_equity_hist와 동일 정의로 통일).
    # (기존엔 Yahoo debtToEquity=유이자부채/자기자본이라 라벨 '부채비율'·차트와 정의가 어긋났음.)
    debt_to_equity = None
    # 유동비율 (Current Ratio)
    current_ratio = _safe_raw(fin_data, "currentRatio")
    # 영업이익률 (Operating Margin)
    operating_margin = _safe_raw(fin_data, "operatingMargins")
    # 잉여현금흐름 (Free Cash Flow)
    fcf = _safe_raw(fin_data, "freeCashflow")
    # 총매출
    total_revenue = _safe_raw(fin_data, "totalRevenue")
    # 총부채
    total_debt = _safe_raw(fin_data, "totalDebt")
    # 총현금
    total_cash = _safe_raw(fin_data, "totalCash")
    # EBITDA
    ebitda = _safe_raw(fin_data, "ebitda")
    # 매출총이익률
    gross_margin = _safe_raw(fin_data, "grossMargins")
    # EV/EBITDA
    ev_to_ebitda = _safe_raw(stats, "enterpriseToEbitda")
    # 베타
    beta = _safe_raw(stats, "beta")

    # 실적 발표일 (calendarEvents 모듈)
    earnings_date = None
    earnings_dates_raw = calendar.get("earnings", {}).get("earningsDate", [])
    if earnings_dates_raw:
        ed = earnings_dates_raw[0]
        if isinstance(ed, dict):
            earnings_date = ed.get("fmt")
        elif isinstance(ed, str):
            earnings_date = ed

    # ── 확장 지표: quoteSummary에서 직접 가져올 수 있는 것들 ──
    bps = _safe_raw(stats, "bookValue")
    net_income_qs = _safe_raw(stats, "netIncomeToCommon")
    revenue_per_share = _safe_raw(fin_data, "revenuePerShare")
    dividend_per_share = _safe_raw(detail, "dividendRate")
    payout_ratio_val = _safe_raw(detail, "payoutRatio")
    if payout_ratio_val is not None:
        payout_ratio_val = round(payout_ratio_val * 100, 2)  # 비율→%로 변환
    eps_growth = _safe_raw(fin_data, "earningsGrowth")
    if eps_growth is not None:
        eps_growth = round(eps_growth * 100, 2)  # 비율→%로 변환
    operating_cashflow = _safe_raw(fin_data, "operatingCashflow")
    shares_outstanding = _safe_raw(stats, "sharesOutstanding")

    # ── 확장 지표: timeseries API로 재무제표 최신 데이터 ──
    # (ts_fields defined above, ts already fetched in parallel)

    def _ts_latest(key):
        """timeseries에서 최신 값 가져오기"""
        pts = ts.get(key, [])
        return pts[-1]["value"] if pts else None

    def _ts_avg2(key):
        """timeseries 최근 2개(기초·기말) 평균. 1개뿐이면 그 값, 없으면 None.
        회전율 등 재무상태표 항목은 기말이 아니라 '평균 잔액'을 쓰는 것이 정석이다."""
        pts = ts.get(key, [])
        vals = [p["value"] for p in pts[-2:] if p.get("value") is not None]
        return sum(vals) / len(vals) if vals else None

    def _ts_ttm(base):
        """손익계산서 흐름 항목의 TTM = 분기 최근 4개 합. 4개 미만이면 None(폴백은 호출측).
        base는 'PretaxIncome'처럼 'quarterly' 접두어를 뗀 이름."""
        pts = [p["value"] for p in ts.get("quarterly" + base, []) if p.get("value") is not None]
        return sum(pts[-4:]) if len(pts) >= 4 else None

    # 절대 지표 (timeseries 우선, quoteSummary fallback)
    # 화면 표시용 순이익: 최신(TTM, netIncomeToCommon) 우선 → 매출(TTM)·마진(TTM)과 정합.
    # 연간 순이익은 tax_rate 폴백 등 '연간끼리' 계산해야 하는 내부용으로 별도 보관.
    net_income_annual = _ts_latest("annualNetIncome")
    net_income = net_income_qs or net_income_annual
    # 표시용 pretax_income·EBIT는 TTM(분기4합) 우선 → net_income(TTM)과 기준 정합.
    # 분기 데이터 부족시 annual 폴백. 단, tax_rate는 '연간끼리' 계산해야 하므로
    # annual pretax(pretax_income_annual)를 별도 보존한다(net_income_annual과 동일 원리).
    pretax_income_annual = _ts_latest("annualPretaxIncome")
    _pretax_ttm = _ts_ttm("PretaxIncome")
    pretax_income = _pretax_ttm if _pretax_ttm is not None else pretax_income_annual
    _ebit_ttm = _ts_ttm("EBIT")
    ebit_val = _ebit_ttm if _ebit_ttm is not None else _ts_latest("annualEBIT")
    interest_expense = _ts_latest("annualInterestExpense")
    income_tax = _ts_latest("annualIncomeTaxExpense")
    accounts_receivable = _ts_latest("annualAccountsReceivable")
    inventory = _ts_latest("annualInventory")
    accounts_payable = _ts_latest("annualAccountsPayable")
    total_assets = _ts_latest("annualTotalAssets")
    current_assets = _ts_latest("annualCurrentAssets")
    current_liabilities = _ts_latest("annualCurrentLiabilities")
    tangible_book = _ts_latest("annualTangibleBookValue")
    operating_income_ts = _ts_latest("annualOperatingIncome")
    capex = _ts_latest("annualCapitalExpenditure")
    ts_revenue = _ts_latest("annualTotalRevenue") or total_revenue

    # 부채비율 = 총부채(TotalLiabilitiesNetMinorityInterest) / 자기자본.
    # 차트 debt_to_equity_hist와 동일한 필드·정의를 써서 카드=차트로 통일한다(값 배수, %아님).
    total_liabilities_ts = _ts_latest("annualTotalLiabilitiesNetMinorityInterest")
    equity_bs = _ts_latest("annualStockholdersEquity")
    if total_liabilities_ts is not None and equity_bs and equity_bs != 0:
        debt_to_equity = total_liabilities_ts / equity_bs

    # 계산 지표
    # 유효세율 (세전이익이 '양수'일 때만 — 적자면 유효세율이 무의미하므로 None)
    # 표시용 pretax_income은 TTM이므로, 여기서는 반드시 annual pretax(pretax_income_annual)와
    # annual net_income으로 '연간끼리' 계산한다(기간 정합).
    tax_rate = None
    if pretax_income_annual and pretax_income_annual > 0:
        if income_tax is not None:
            tax_rate = round(income_tax / pretax_income_annual * 100, 2)
        elif net_income_annual is not None:
            tax_rate = round((1 - net_income_annual / pretax_income_annual) * 100, 2)

    # 운전자본
    working_capital = None
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities

    # 자산회전율 (매출 ÷ 평균 총자산)
    asset_turnover = None
    avg_assets = _ts_avg2("annualTotalAssets")
    if ts_revenue and avg_assets and avg_assets != 0:
        asset_turnover = round(ts_revenue / avg_assets, 2)

    # 재고회전율 (매출원가 ÷ 평균 재고)
    inventory_turnover = None
    cost_of_revenue = _ts_latest("annualCostOfRevenue")
    avg_inventory = _ts_avg2("annualInventory")
    if cost_of_revenue and avg_inventory and avg_inventory != 0:
        inventory_turnover = round(cost_of_revenue / avg_inventory, 1)

    # 매출채권회전율 (매출 ÷ 평균 매출채권)
    receivables_turnover = None
    avg_ar = _ts_avg2("annualAccountsReceivable")
    if ts_revenue and avg_ar and avg_ar != 0:
        receivables_turnover = round(ts_revenue / avg_ar, 1)

    # ROIC = NOPAT ÷ 투하자본(총자산 − 유동부채). NOPAT = 영업이익 × (1 - 유효세율).
    # 투하자본 정의를 연간/분기 차트(roic_hist·roic_quarterly)와 동일하게 통일 —
    # 이전엔 카드만 (유이자부채+자기자본)이라 같은 지표가 화면마다 다른 값이었다.
    # (영업손실이면 ROIC 음수가 정상이므로 영업이익 부호는 막지 않는다. 투하자본만 양수 요구.)
    roic = None
    if (operating_income_ts is not None and total_assets is not None
            and current_liabilities is not None):
        invested_capital = total_assets - current_liabilities
        if invested_capital > 0:
            # 유효세율은 0~100% 범위일 때만 사용, 아니면 미국 법인세 근사 21%
            effective_tax = tax_rate / 100 if (tax_rate is not None and 0 <= tax_rate <= 100) else 0.21
            nopat = operating_income_ts * (1 - effective_tax)
            roic = round(nopat / invested_capital * 100, 2)

    # 영업CF마진 = 영업현금흐름 ÷ 매출.
    # operating_cashflow(financialData)는 TTM이므로 분모도 TTM 매출(total_revenue)로 맞춘다
    # (연간 ts_revenue를 쓰면 성장기업일수록 분모가 작아 마진이 과대됨 — 다른 마진 3형제도 전부 TTM).
    # total_revenue(TTM)가 없을 때만 연간으로 폴백.
    ocf_margin = None
    rev_for_ocf = total_revenue or ts_revenue
    if operating_cashflow and rev_for_ocf and rev_for_ocf != 0:
        ocf_margin = round(operating_cashflow / rev_for_ocf, 4)  # 비율로 저장 (프론트에서 *100)

    # 설비투자비율
    capex_to_revenue = None
    if capex and ts_revenue and ts_revenue != 0:
        capex_to_revenue = round(abs(capex) / ts_revenue * 100, 2)

    # 순이익성장률 & 영업이익성장률 (YoY)
    net_income_growth = None
    operating_income_growth = None
    ni_pts = ts.get("annualNetIncome", [])
    if len(ni_pts) >= 2:
        prev_ni = ni_pts[-2]["value"]
        curr_ni = ni_pts[-1]["value"]
        if prev_ni and prev_ni != 0:
            net_income_growth = round((curr_ni - prev_ni) / abs(prev_ni) * 100, 2)

    oi_pts = ts.get("annualOperatingIncome", [])
    if len(oi_pts) >= 2:
        prev_oi = oi_pts[-2]["value"]
        curr_oi = oi_pts[-1]["value"]
        if prev_oi and prev_oi != 0:
            operating_income_growth = round((curr_oi - prev_oi) / abs(prev_oi) * 100, 2)

    # 섹터별 핵심 지표 목록 (AI 프로필이 없을 때 폴백용)
    key_metrics = SECTOR_KEY_METRICS.get(sector, [])

    result = {
        "ticker": ticker.upper(),
        "name": meta.get("longName") or meta.get("shortName") or profile.get("longName") or ticker.upper(),
        "sector": sector,
        "industry": industry,
        "country": country,
        "current_price": meta.get("regularMarketPrice") or current_price_fin,
        "market_cap": market_cap,
        "pe_ratio": pe_ratio,
        "forward_pe": forward_pe,
        "eps": eps,
        "pb_ratio": pb_ratio,
        "dividend_yield": dividend_yield,
        "52w_high": meta.get("fiftyTwoWeekHigh") or _safe_raw(detail, "fiftyTwoWeekHigh"),
        "52w_low": meta.get("fiftyTwoWeekLow") or _safe_raw(detail, "fiftyTwoWeekLow"),
        "target_high": target_high,
        "target_low": target_low,
        "target_mean": target_mean,
        "revenue_growth": revenue_growth,
        "profit_margin": profit_margin,
        "operating_margin": operating_margin,
        "gross_margin": gross_margin,
        "roe": roe,
        "roa": roa,
        "debt_to_equity": debt_to_equity,
        "current_ratio": current_ratio,
        "fcf": fcf,
        "total_revenue": total_revenue,
        "total_debt": total_debt,
        "total_cash": total_cash,
        "ebitda": ebitda,
        "ev_to_ebitda": ev_to_ebitda,
        "beta": beta,
        "description": description,
        "earnings_date": earnings_date,
        "key_metrics": key_metrics,
        # ── 확장 지표 ──
        "net_income": net_income,
        "pretax_income": pretax_income,
        "ebit": ebit_val,
        "interest_expense": interest_expense,
        "tax_rate": tax_rate,
        "bps": bps,
        "accounts_receivable": accounts_receivable,
        "inventory": inventory,
        "accounts_payable": accounts_payable,
        "working_capital": working_capital,
        "tangible_book": tangible_book,
        "asset_turnover": asset_turnover,
        "inventory_turnover": inventory_turnover,
        "receivables_turnover": receivables_turnover,
        "roic": roic,
        "ocf_margin": ocf_margin,
        "capex_to_revenue": capex_to_revenue,
        "revenue_per_share": revenue_per_share,
        "dividend_per_share": dividend_per_share,
        "payout_ratio": payout_ratio_val,
        "eps_growth": eps_growth,
        "net_income_growth": net_income_growth,
        "operating_income_growth": operating_income_growth,
    }

    # Cache the result before returning
    _overview_cache[ticker_upper] = {"data": result, "ts": time.time()}
    return result


def get_price_history(ticker: str, period: str = "1y"):
    interval_map = {
        "1d": "5m", "3d": "15m", "5d": "30m",
        "1mo": "1d", "3mo": "1d", "6mo": "1d",
        "1y": "1d", "2y": "1wk", "3y": "1wk",
        "5y": "1wk", "10y": "1mo", "max": "1mo"
    }
    interval = interval_map.get(period, "1d")
    data = _yf_chart(ticker, interval=interval, range_=period)
    if not data:
        return []
    try:
        result_data = data["chart"]["result"][0]
        timestamps = result_data["timestamp"]
        ohlcv = result_data["indicators"]["quote"][0]
        from datetime import datetime
        rows = []
        use_time = period in ("1d", "3d", "5d")
        for i, ts in enumerate(timestamps):
            try:
                dt = datetime.utcfromtimestamp(ts)
                date_str = dt.strftime("%Y-%m-%d %H:%M") if use_time else dt.strftime("%Y-%m-%d")
                rows.append({
                    "date": date_str,
                    "open":   round(ohlcv["open"][i] or 0, 2),
                    "high":   round(ohlcv["high"][i] or 0, 2),
                    "low":    round(ohlcv["low"][i] or 0, 2),
                    "close":  round(ohlcv["close"][i] or 0, 2),
                    "volume": int(ohlcv["volume"][i] or 0),
                })
            except (TypeError, IndexError):
                continue
        return rows
    except (KeyError, IndexError):
        return []


def get_financials(ticker: str):
    result = {}

    summary = _yf_quoteSummary(
        ticker,
        "incomeStatementHistory,balanceSheetHistory,cashflowStatementHistory,"
        "incomeStatementHistoryQuarterly,balanceSheetHistoryQuarterly,cashflowStatementHistoryQuarterly"
    )

    def _parse_statements(module_key, list_key):
        try:
            stmts = summary.get(module_key, {}).get(list_key, [])
            rows = {}
            for stmt in stmts:
                date = stmt.get("endDate", {}).get("fmt", "N/A")
                for key, val in stmt.items():
                    if key in ("endDate", "maxAge"):
                        continue
                    raw = val.get("raw") if isinstance(val, dict) else val
                    if key not in rows:
                        rows[key] = {}
                    rows[key][date] = raw
            return rows
        except Exception:
            return {}

    result["income_statement"] = _parse_statements("incomeStatementHistory", "incomeStatementHistory")
    result["balance_sheet"] = _parse_statements("balanceSheetHistory", "balanceSheetStatements")
    result["cash_flow"] = _parse_statements("cashflowStatementHistory", "cashflowStatements")
    result["income_statement_quarterly"] = _parse_statements("incomeStatementHistoryQuarterly", "incomeStatementHistory")
    result["balance_sheet_quarterly"] = _parse_statements("balanceSheetHistoryQuarterly", "balanceSheetStatements")
    result["cash_flow_quarterly"] = _parse_statements("cashflowStatementHistoryQuarterly", "cashflowStatements")

    return result


def _yf_timeseries(ticker: str, fields: list, period1: str = "315532800", period2: str = "1900000000"):
    """Yahoo Finance fundamentals-timeseries API 호출"""
    url = f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker.upper()}"
    params = {
        "type": ",".join(fields),
        "period1": period1,
        "period2": period2,
        "merge": "false",
    }
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = {}
        for item in data.get("timeseries", {}).get("result", []):
            data_keys = [k for k in item.keys() if k not in ("meta", "timestamp")]
            if data_keys:
                key = data_keys[0]
                vals = item.get(key) or []
                points = []
                for v in vals:
                    date = v.get("asOfDate")
                    raw = v.get("reportedValue", {}).get("raw")
                    if date and raw is not None:
                        points.append({"date": date, "value": raw})
                points.sort(key=lambda x: x["date"])
                result[key] = points
        return result
    except Exception:
        return {}


def _get_price_at_dates(ticker: str, dates: list):
    """주어진 날짜들에 가장 가까운 종가를 반환 (dict: date → close)"""
    import datetime
    chart = _yf_chart(ticker, interval="1mo", range_="max")
    if not chart:
        return {}
    result_data = chart.get("chart", {}).get("result", [{}])[0]
    timestamps = result_data.get("timestamp", [])
    closes = result_data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    if not timestamps or not closes:
        return {}

    # timestamp → (date_str, close) 매핑
    price_map = {}
    for ts, close in zip(timestamps, closes):
        if close is not None:
            dt = datetime.datetime.fromtimestamp(ts)
            price_map[dt.strftime("%Y-%m")] = close

    # 각 요청 날짜에 대해 가장 가까운 월의 종가 찾기
    result = {}
    for date_str in dates:
        ym = date_str[:7]  # "2023-09-30" → "2023-09"
        if ym in price_map:
            result[date_str] = price_map[ym]
        else:
            # 앞뒤 1개월 탐색
            try:
                dt = datetime.datetime.strptime(date_str[:7], "%Y-%m")
                for delta in [1, -1, 2, -2]:
                    check = dt + datetime.timedelta(days=30 * delta)
                    check_ym = check.strftime("%Y-%m")
                    if check_ym in price_map:
                        result[date_str] = price_map[check_ym]
                        break
            except Exception:
                pass
    return result


# ── SEC 장기 히스토리 병합 (차트) ──────────────────────────────────
# Yahoo timeseries는 연간 ~4년만 주므로, SEC XBRL(10년+)을 회계연도 기준으로 병합해
# 차트가 장기 히스토리를 그리게 한다. 외국 filer/미보고 블록은 빈 결과 → Yahoo 값 그대로(폴백).
_sec_blocks_cache = {}
_SEC_BLOCKS_TTL = 6 * 3600  # SEC 연간 데이터는 드물게 갱신 → 6시간 캐시

# (metrics의 연간 절대지표 키, SEC building-block 키, Yahoo 연간 timeseries 필드).
# 액면분할에 민감한 EPS/주식수/주당지표는 제외(SEC 원본 vs Yahoo 분할조정 → 병합 시 불연속).
# EBITDA/FCF/total_debt/tangible_book은 SEC 단일개념이 없어 다음 단계에서 유도 처리.
_SEC_ABS_MERGE = [
    ("revenue", "revenue", "annualTotalRevenue"),
    ("net_income", "net_income", "annualNetIncome"),
    ("operating_income", "operating_income", "annualOperatingIncome"),
    ("gross_profit", "gross_profit", "annualGrossProfit"),
    ("total_assets", "total_assets", "annualTotalAssets"),
    ("equity_hist", "stockholders_equity", "annualStockholdersEquity"),
    ("liabilities_hist", "total_liabilities", "annualTotalLiabilitiesNetMinorityInterest"),
    ("total_cash_hist", "cash", "annualCashAndCashEquivalents"),
    ("pretax_income", "pretax_income", "annualPretaxIncome"),
    ("interest_expense_hist", "interest_expense", "annualInterestExpense"),
    ("accounts_receivable_hist", "accounts_receivable", "annualAccountsReceivable"),
    ("inventory_hist", "inventory", "annualInventory"),
    ("accounts_payable_hist", "accounts_payable", "annualAccountsPayable"),
]


def _get_sec_blocks_cached(ticker: str):
    """SEC building-block을 TTL 캐시로 가져온다(느린 다중 HTTP를 반복하지 않도록)."""
    from app.services import sec_client
    key = ticker.upper()
    now = time.time()
    c = _sec_blocks_cache.get(key)
    if c and now - c["ts"] < _SEC_BLOCKS_TTL:
        return c["data"]
    try:
        data = sec_client.get_sec_building_blocks(ticker)
    except Exception:
        data = {}
    _sec_blocks_cache[key] = {"ts": now, "data": data}
    return data


def _merge_sec_annual_history(ticker, metrics, get_yahoo):
    """metrics의 연간 절대지표를 SEC 장기값과 병합해 확장한다(제자리 수정).

    get_yahoo: get_metric_history 내부의 _get 클로저(Yahoo 연간 시계열 접근).
    SEC가 없거나(외국 filer) 특정 블록 미보고면 해당 지표는 그대로 둔다(Yahoo 폴백).
    """
    from app.services import sec_client
    blocks = _get_sec_blocks_cached(ticker)
    if not blocks:
        return
    for mkey, blk, yfield in _SEC_ABS_MERGE:
        sec_series = blocks.get(blk) or []
        if not sec_series:
            continue
        merged = sec_client.merge_annual_by_fy(sec_series, get_yahoo(yfield))
        if merged:
            metrics[mkey] = merged


# 비율/유도 지표를 계산할 building-block(키) → Yahoo 연간 필드.
_SEC_RATIO_YFIELD = {
    "revenue": "annualTotalRevenue",
    "net_income": "annualNetIncome",
    "operating_income": "annualOperatingIncome",
    "gross_profit": "annualGrossProfit",
    "total_assets": "annualTotalAssets",
    "stockholders_equity": "annualStockholdersEquity",
    "total_liabilities": "annualTotalLiabilitiesNetMinorityInterest",
    "pretax_income": "annualPretaxIncome",
    "accounts_receivable": "annualAccountsReceivable",
    "inventory": "annualInventory",
    "current_assets": "annualCurrentAssets",
    "current_liabilities": "annualCurrentLiabilities",
    "operating_cash_flow": "annualOperatingCashFlow",
    "capex": "annualCapitalExpenditure",
    "dividends_paid": "annualCashDividendsPaid",
}


def _derive_sec_ratios(ticker, metrics, get_yahoo):
    """병합된 절대값(SEC⊕Yahoo)에서 연간 비율/유도 지표를 재계산해 장기화한다(제자리 수정).

    겹치는 최근 연도는 SEC=Yahoo로 값이 정합하므로 기존과 동일하고, 옛 연도만 확장된다.
    외국 filer 등 SEC가 없으면 병합=Yahoo(4년)와 같아 결과가 사실상 불변(무해).
    회계연도(date 앞 4자리) 기준으로 분자·분모를 맞춰 SEC/Yahoo 날짜 미세차이를 흡수한다.
    """
    from app.services import sec_client
    blocks = _get_sec_blocks_cached(ticker)
    if not blocks:
        return

    # 각 블록의 SEC⊕Yahoo 병합을 fy(int) -> {"date","value"} 로
    M = {}
    for blk, yfield in _SEC_RATIO_YFIELD.items():
        series = sec_client.merge_annual_by_fy(blocks.get(blk) or [], get_yahoo(yfield))
        M[blk] = {int(p["date"][:4]): p for p in series if p.get("date")}

    def _ratio(num_blk, den_blk, key, multiply=100, num_abs=False, den_positive=False,
               avg_den=False):
        # avg_den=True: 분모를 (전기말+당기말)/2 평균 잔액으로 — 회전율 지표는
        # 카드(overview)가 평균 잔액을 쓰므로 차트도 동일 공식으로 통일한다.
        num, den = M.get(num_blk, {}), M.get(den_blk, {})
        res = []
        den_fys = sorted(den)
        for fy in sorted(set(num) & set(den)):
            n, d = num[fy]["value"], den[fy]["value"]
            if avg_den and d is not None:
                idx = den_fys.index(fy)
                if idx > 0:
                    prev = den[den_fys[idx - 1]]["value"]
                    if prev is not None:
                        d = (d + prev) / 2
            if n is None or d is None or d == 0:
                continue
            if den_positive and d <= 0:
                continue
            if num_abs:
                n = abs(n)
            res.append({"date": num[fy]["date"], "value": round(n / d * multiply, 2)})
        if res:
            metrics[key] = res

    _ratio("net_income", "revenue", "profit_margin_hist")
    _ratio("operating_income", "revenue", "operating_margin_hist")
    _ratio("gross_profit", "revenue", "gross_margin_hist")
    _ratio("net_income", "stockholders_equity", "roe_hist")
    _ratio("net_income", "total_assets", "roa_hist")
    _ratio("total_liabilities", "stockholders_equity", "debt_to_equity_hist", multiply=1)
    # 회전율 3종: 카드(overview)와 동일 공식으로 통일 —
    # 재고회전율 분자는 매출이 아니라 '매출원가'(회계 표준), 분모는 평균 잔액.
    _ratio("revenue", "total_assets", "asset_turnover_hist", multiply=1, avg_den=True)
    _ratio("cost_of_revenue", "inventory", "inventory_turnover_hist", multiply=1, avg_den=True)
    _ratio("revenue", "accounts_receivable", "receivables_turnover_hist", multiply=1, avg_den=True)
    _ratio("operating_cash_flow", "revenue", "ocf_margin_hist")
    _ratio("capex", "revenue", "capex_to_revenue_hist", num_abs=True)
    _ratio("dividends_paid", "net_income", "payout_ratio_hist", num_abs=True, den_positive=True)

    # 유효세율 = (1 - net_income/pretax) * 100  (pretax != 0)
    ni, pti = M.get("net_income", {}), M.get("pretax_income", {})
    tax_by_fy, tax_res = {}, []
    for fy in sorted(set(ni) & set(pti)):
        n, p = ni[fy]["value"], pti[fy]["value"]
        if n is not None and p not in (None, 0):
            v = round((1 - n / p) * 100, 2)
            tax_by_fy[fy] = v
            tax_res.append({"date": pti[fy]["date"], "value": v})
    if tax_res:
        metrics["tax_rate_hist"] = tax_res

    # FCF = 영업현금흐름 - capex. capex 블록은 이미 음수(negate)로 정규화되어 있으므로 더한다.
    # (AAPL/KO에서 Yahoo annualFreeCashFlow와 정확 일치 검증. EBITDA/tangible_book/total_debt는
    #  SEC 유도가 Yahoo와 정합하지 않아 확장하지 않음 → 해당 차트는 Yahoo 4년 유지.)
    ocf_m, cx_m = M.get("operating_cash_flow", {}), M.get("capex", {})
    fcf = [{"date": ocf_m[fy]["date"], "value": round(ocf_m[fy]["value"] + cx_m[fy]["value"], 0)}
           for fy in sorted(set(ocf_m) & set(cx_m))
           if ocf_m[fy]["value"] is not None and cx_m[fy]["value"] is not None]
    if fcf:
        metrics["fcf_hist"] = fcf

    # 운전자본 = 유동자산 - 유동부채
    ca, cl = M.get("current_assets", {}), M.get("current_liabilities", {})
    wc = [{"date": ca[fy]["date"], "value": round(ca[fy]["value"] - cl[fy]["value"], 0)}
          for fy in sorted(set(ca) & set(cl))
          if ca[fy]["value"] is not None and cl[fy]["value"] is not None]
    if wc:
        metrics["working_capital_hist"] = wc

    # ROIC = NOPAT / (총자산 - 유동부채), NOPAT = 영업이익 × (1 - 유효세율(없으면 21%))
    oi, ta = M.get("operating_income", {}), M.get("total_assets", {})
    roic = []
    for fy in sorted(set(oi) & set(ta) & set(cl)):
        o, t, c = oi[fy]["value"], ta[fy]["value"], cl[fy]["value"]
        if o is None or t is None or c is None:
            continue
        ic = t - c
        if ic <= 0:
            continue
        tax_r = tax_by_fy.get(fy, 21) / 100
        roic.append({"date": oi[fy]["date"], "value": round(o * (1 - tax_r) / ic * 100, 2)})
    if roic:
        metrics["roic_hist"] = roic

    # 성장률(YoY) = (당기-전기)/|전기| × 100
    def _growth(blk, key):
        m = M.get(blk, {})
        fys = sorted(m)
        res = []
        for i in range(1, len(fys)):
            prev, curr = m[fys[i - 1]]["value"], m[fys[i]]["value"]
            if prev not in (None, 0) and curr is not None:
                res.append({"date": m[fys[i]]["date"], "value": round((curr - prev) / abs(prev) * 100, 2)})
        if res:
            metrics[key] = res

    _growth("net_income", "net_income_growth_hist")
    _growth("operating_income", "operating_income_growth_hist")


def _get_splits(ticker):
    """Yahoo 액면분할 이력 {날짜(YYYY-MM-DD): 비율}. 실패시 {}.
    오래된(1970년 이전) 음수 timestamp도 안전하게 파싱한다."""
    import datetime
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}",
            headers=HEADERS,
            params={"range": "max", "interval": "3mo", "events": "splits"},
            timeout=15,
        )
        r.raise_for_status()
        ev = r.json()["chart"]["result"][0].get("events", {}).get("splits", {})
    except Exception:
        return {}
    epoch = datetime.datetime(1970, 1, 1)
    out = {}
    for s in ev.values():
        try:
            d = (epoch + datetime.timedelta(seconds=s["date"])).strftime("%Y-%m-%d")
            den = s.get("denominator")
            if den:
                out[d] = s["numerator"] / den
        except Exception:
            continue
    return out


def _extend_eps_with_sec(ticker, metrics):
    """eps_hist(Yahoo ~4년)를 SEC as-reported EPS를 분할조정해 장기화한다(제자리 수정).

    SEC EPS는 filed(보고)일까지의 분할이 이미 반영돼 있으므로 'filed일 이후' 분할로만 나눈다
    (period end 기준으로 조정하면 이중조정 → 분할 지점 단차 발생. filed 기준이라야 매끄럽다).
    확장된 EPS에서 eps_growth_hist(YoY)도 재계산한다. 외국 filer 등 SEC EPS 없으면 그대로 둔다.
    """
    from app.services import sec_client
    raw = sec_client.get_annual_eps_diluted_with_filed(ticker)
    if not raw:
        return
    splits = _get_splits(ticker)  # {date: ratio}
    adjusted = sec_client.split_adjust_by_filed(raw, splits)
    merged = sec_client.merge_annual_by_fy(adjusted, metrics.get("eps_hist", []))
    if not merged:
        return
    metrics["eps_hist"] = merged
    # eps_growth도 확장된 EPS에서 재계산(YoY)
    m = {int(p["date"][:4]): p for p in merged if p.get("date")}
    fys = sorted(m)
    growth = []
    for i in range(1, len(fys)):
        prev, curr = m[fys[i - 1]]["value"], m[fys[i]]["value"]
        if prev not in (None, 0) and curr is not None:
            growth.append({"date": m[fys[i]]["date"], "value": round((curr - prev) / abs(prev) * 100, 2)})
    if growth:
        metrics["eps_growth_hist"] = growth


def _extend_per_share_with_sec(ticker, metrics):
    """주당지표(주당매출·BPS·주당배당)를 SEC로 장기화한다(제자리 수정).

    이 지표들은 '달러값 ÷ 주식수'인데 달러값(매출·자기자본·배당)은 이미 SEC 장기 확보돼 있고
    주식수만 분할 민감하다. 그래서 기말주식수를 '현재 기준'으로 분할조정(multiply)한 뒤 나눈다.
    주식수 없으면(외국 filer 등) 그대로 둔다(Yahoo 폴백).
    """
    from app.services import sec_client
    shares_raw = sec_client.get_annual_shares_with_filed(ticker)
    if not shares_raw:
        return
    splits = _get_splits(ticker)
    adj = sec_client.split_adjust_by_filed(shares_raw, splits, multiply=True)  # 현재 기준 주식수
    shares_by_fy = {p["fy"]: p for p in adj if p.get("value")}
    if not shares_by_fy:
        return
    blocks = _get_sec_blocks_cached(ticker)

    def _per_share(block_key, metric_key, absval=False):
        series = blocks.get(block_key) or []
        sec_ps = []
        for p in series:
            sh = shares_by_fy.get(p["fy"])
            if sh and sh["value"] and p.get("value") is not None:
                v = abs(p["value"]) if absval else p["value"]
                sec_ps.append({"fy": p["fy"], "end": p["end"], "value": round(v / sh["value"], 4)})
        if sec_ps:
            merged = sec_client.merge_annual_by_fy(sec_ps, metrics.get(metric_key, []))
            if merged:
                metrics[metric_key] = merged

    _per_share("revenue", "revenue_per_share_hist")
    _per_share("stockholders_equity", "bps_hist")
    _per_share("dividends_paid", "dividend_per_share_hist", absval=True)


def get_metric_history(ticker: str):
    """주요 재무 지표의 연도별/분기별 히스토리를 timeseries API로 반환"""

    # 연간 데이터 필드
    annual_fields = [
        "annualTotalRevenue", "annualNetIncome", "annualOperatingIncome",
        "annualGrossProfit", "annualTotalAssets",
        "annualTotalLiabilitiesNetMinorityInterest", "annualStockholdersEquity",
        "annualOperatingCashFlow", "annualCapitalExpenditure", "annualFreeCashFlow",
        "annualEBITDA", "annualTotalDebt", "annualCashAndCashEquivalents",
        "annualBasicEPS", "annualDilutedEPS", "annualOrdinarySharesNumber",
        # 추가: 시가총액, 유동비율, 배당, EV 관련
        "annualMarketCap", "annualEnterpriseValue",
        "annualCurrentAssets", "annualCurrentLiabilities",
        "annualCashDividendsPaid",
        # 추가: 확장 재무 지표
        "annualPretaxIncome", "annualEBIT", "annualInterestExpense",
        "annualAccountsReceivable", "annualInventory", "annualAccountsPayable",
        "annualTangibleBookValue",
        # 재고회전율 분자 (카드와 동일하게 매출원가 기준)
        "annualCostOfRevenue",
    ]
    # 분기별 데이터 필드 (연간과 동일 항목)
    quarterly_fields = [
        "quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyOperatingIncome",
        "quarterlyGrossProfit", "quarterlyTotalAssets",
        "quarterlyTotalLiabilitiesNetMinorityInterest", "quarterlyStockholdersEquity",
        "quarterlyOperatingCashFlow", "quarterlyCapitalExpenditure", "quarterlyFreeCashFlow",
        "quarterlyEBITDA", "quarterlyTotalDebt", "quarterlyCashAndCashEquivalents",
        "quarterlyBasicEPS", "quarterlyDilutedEPS", "quarterlyOrdinarySharesNumber",
        # 추가
        "quarterlyMarketCap", "quarterlyEnterpriseValue",
        "quarterlyCurrentAssets", "quarterlyCurrentLiabilities",
        "quarterlyCashDividendsPaid",
        # 추가: 확장 재무 지표
        "quarterlyPretaxIncome", "quarterlyEBIT", "quarterlyInterestExpense",
        "quarterlyAccountsReceivable", "quarterlyInventory", "quarterlyAccountsPayable",
        "quarterlyTangibleBookValue",
        # 재고회전율 분자 (카드와 동일하게 매출원가 기준)
        "quarterlyCostOfRevenue",
    ]

    # 필드가 많으면 API가 잘라버리므로 배치로 나눠서 요청
    all_fields = annual_fields + quarterly_fields
    raw = {}
    batch_size = 20
    for i in range(0, len(all_fields), batch_size):
        batch = all_fields[i:i + batch_size]
        batch_result = _yf_timeseries(ticker, batch)
        raw.update(batch_result)

    # 헬퍼: raw 키에서 값 추출
    def _get(key):
        return raw.get(key, [])

    # 헬퍼: 두 시리즈의 비율 계산 (날짜 기준 매칭)
    def _ratio(numerator_key, denominator_key, multiply=100, avg_den=False):
        # avg_den=True: 분모를 (전기말+당기말)/2 평균 잔액으로 — 회전율 지표는
        # 카드(overview)가 평균 잔액을 쓰므로 차트도 동일 공식으로 통일한다.
        nums = {p["date"]: p["value"] for p in _get(numerator_key)}
        dens = {p["date"]: p["value"] for p in _get(denominator_key)}
        result = []
        den_dates = sorted(dens.keys())
        for date in sorted(nums.keys()):
            n = nums.get(date)
            d = dens.get(date)
            if avg_den and d is not None:
                idx = den_dates.index(date) if date in den_dates else -1
                if idx > 0:
                    prev = dens.get(den_dates[idx - 1])
                    if prev is not None:
                        d = (d + prev) / 2
            if n is not None and d is not None and d != 0:
                result.append({"date": date, "value": round(n / d * multiply, 2)})
        return result

    metrics = {}

    # ── 절대값 지표 (연간) ──
    metrics["revenue"] = _get("annualTotalRevenue")
    metrics["net_income"] = _get("annualNetIncome")
    metrics["operating_income"] = _get("annualOperatingIncome")
    metrics["gross_profit"] = _get("annualGrossProfit")
    metrics["total_assets"] = _get("annualTotalAssets")
    metrics["total_debt_hist"] = _get("annualTotalDebt")
    metrics["total_cash_hist"] = _get("annualCashAndCashEquivalents")
    metrics["ebitda_hist"] = _get("annualEBITDA")
    metrics["fcf_hist"] = _get("annualFreeCashFlow")
    metrics["equity_hist"] = _get("annualStockholdersEquity")
    metrics["liabilities_hist"] = _get("annualTotalLiabilitiesNetMinorityInterest")

    # ── EPS 히스토리 (직접 제공) ──
    metrics["eps_hist"] = _get("annualDilutedEPS") or _get("annualBasicEPS")
    metrics["eps_quarterly"] = _get("quarterlyDilutedEPS") or _get("quarterlyBasicEPS")

    # ── PER, PBR 계산 (주가 필요) ──
    # 연간 EPS 날짜 + 연간 자기자본 날짜의 주가를 가져옴
    eps_data = metrics["eps_hist"]
    equity_data = _get("annualStockholdersEquity")
    shares_data = _get("annualOrdinarySharesNumber")
    all_dates = set()
    for d in eps_data:
        all_dates.add(d["date"])
    for d in equity_data:
        all_dates.add(d["date"])

    if all_dates:
        price_map = _get_price_at_dates(ticker, list(all_dates))

        # PER = 주가 / EPS
        eps_dict = {p["date"]: p["value"] for p in eps_data}
        per_hist = []
        for date in sorted(eps_dict.keys()):
            eps_val = eps_dict.get(date)
            price = price_map.get(date)
            if eps_val and price and eps_val > 0:
                per_hist.append({"date": date, "value": round(price / eps_val, 2)})
        metrics["per_hist"] = per_hist

        # PBR = 주가 / 주당순자산 (BPS = 자기자본 / 발행주식수)
        equity_dict = {p["date"]: p["value"] for p in equity_data}
        shares_dict = {p["date"]: p["value"] for p in shares_data}
        pbr_hist = []
        for date in sorted(equity_dict.keys()):
            eq = equity_dict.get(date)
            sh = shares_dict.get(date)
            price = price_map.get(date)
            if eq and sh and price and sh > 0:
                bps = eq / sh
                if bps > 0:
                    pbr_hist.append({"date": date, "value": round(price / bps, 2)})
        metrics["pbr_hist"] = pbr_hist

    # ── 확장 절대값 지표 (연간) ──
    metrics["pretax_income"] = _get("annualPretaxIncome")
    metrics["ebit_hist"] = _get("annualEBIT")
    metrics["interest_expense_hist"] = _get("annualInterestExpense")
    metrics["accounts_receivable_hist"] = _get("annualAccountsReceivable")
    metrics["inventory_hist"] = _get("annualInventory")
    metrics["accounts_payable_hist"] = _get("annualAccountsPayable")
    metrics["tangible_book_hist"] = _get("annualTangibleBookValue")

    # 운전자본 (CurrentAssets - CurrentLiabilities)
    ca_a = {p["date"]: p["value"] for p in _get("annualCurrentAssets")}
    cl_a = {p["date"]: p["value"] for p in _get("annualCurrentLiabilities")}
    wc_hist = []
    for date in sorted(ca_a.keys()):
        ca_val = ca_a.get(date)
        cl_val = cl_a.get(date)
        if ca_val is not None and cl_val is not None:
            wc_hist.append({"date": date, "value": round(ca_val - cl_val, 0)})
    metrics["working_capital_hist"] = wc_hist

    # BPS (자기자본 / 발행주식수)
    equity_a_dict = {p["date"]: p["value"] for p in equity_data}
    shares_a_dict = {p["date"]: p["value"] for p in shares_data}
    bps_hist = []
    for date in sorted(equity_a_dict.keys()):
        eq = equity_a_dict.get(date)
        sh = shares_a_dict.get(date)
        if eq is not None and sh and sh > 0:
            bps_hist.append({"date": date, "value": round(eq / sh, 2)})
    metrics["bps_hist"] = bps_hist

    # 주당매출 (Revenue / Shares)
    rev_a = {p["date"]: p["value"] for p in _get("annualTotalRevenue")}
    rps_hist = []
    for date in sorted(rev_a.keys()):
        r = rev_a.get(date)
        sh = shares_a_dict.get(date)
        if r is not None and sh and sh > 0:
            rps_hist.append({"date": date, "value": round(r / sh, 2)})
    metrics["revenue_per_share_hist"] = rps_hist

    # 주당배당금 (|CashDividendsPaid| / Shares)
    div_paid_a = {p["date"]: abs(p["value"]) for p in _get("annualCashDividendsPaid") if p.get("value")}
    dps_hist = []
    for date in sorted(div_paid_a.keys()):
        d = div_paid_a.get(date)
        sh = shares_a_dict.get(date)
        if d is not None and sh and sh > 0:
            dps_hist.append({"date": date, "value": round(d / sh, 2)})
    metrics["dividend_per_share_hist"] = dps_hist

    # ── 비율 지표 (연간) ── → % 단위로 반환
    metrics["profit_margin_hist"] = _ratio("annualNetIncome", "annualTotalRevenue")
    metrics["operating_margin_hist"] = _ratio("annualOperatingIncome", "annualTotalRevenue")
    metrics["gross_margin_hist"] = _ratio("annualGrossProfit", "annualTotalRevenue")
    metrics["roe_hist"] = _ratio("annualNetIncome", "annualStockholdersEquity")
    metrics["roa_hist"] = _ratio("annualNetIncome", "annualTotalAssets")
    metrics["debt_to_equity_hist"] = _ratio(
        "annualTotalLiabilitiesNetMinorityInterest", "annualStockholdersEquity", multiply=1
    )  # 비율(배수)이므로 multiply=1

    # ── 확장 비율 지표 (연간) ──
    # 유효세율 (1 - NetIncome/PretaxIncome) * 100
    ni_a = {p["date"]: p["value"] for p in _get("annualNetIncome")}
    pti_a = {p["date"]: p["value"] for p in _get("annualPretaxIncome")}
    tax_hist = []
    for date in sorted(pti_a.keys()):
        ni = ni_a.get(date)
        pti = pti_a.get(date)
        if ni is not None and pti is not None and pti != 0:
            tax_hist.append({"date": date, "value": round((1 - ni / pti) * 100, 2)})
    metrics["tax_rate_hist"] = tax_hist

    # 회전율 3종: 카드(overview)와 동일 공식으로 통일 —
    # 재고회전율 분자는 매출이 아니라 '매출원가'(회계 표준), 분모는 평균 잔액.
    metrics["asset_turnover_hist"] = _ratio("annualTotalRevenue", "annualTotalAssets", multiply=1, avg_den=True)
    metrics["inventory_turnover_hist"] = _ratio("annualCostOfRevenue", "annualInventory", multiply=1, avg_den=True)
    metrics["receivables_turnover_hist"] = _ratio("annualTotalRevenue", "annualAccountsReceivable", multiply=1, avg_den=True)
    metrics["ocf_margin_hist"] = _ratio("annualOperatingCashFlow", "annualTotalRevenue")

    # 설비투자비율 (|CapEx| / Revenue * 100)
    capex_a = {p["date"]: p["value"] for p in _get("annualCapitalExpenditure")}
    capex_ratio_hist = []
    for date in sorted(rev_a.keys()):
        cx = capex_a.get(date)
        rv = rev_a.get(date)
        if cx is not None and rv and rv != 0:
            capex_ratio_hist.append({"date": date, "value": round(abs(cx) / rv * 100, 2)})
    metrics["capex_to_revenue_hist"] = capex_ratio_hist

    # 배당성향 (|CashDividendsPaid| / NetIncome * 100)
    payout_hist = []
    for date in sorted(div_paid_a.keys()):
        d = div_paid_a.get(date)
        ni = ni_a.get(date)
        if d is not None and ni and ni > 0:
            payout_hist.append({"date": date, "value": round(d / ni * 100, 2)})
    metrics["payout_ratio_hist"] = payout_hist

    # ROIC = NOPAT / InvestedCapital
    # NOPAT = OperatingIncome * (1 - effectiveTaxRate)
    # InvestedCapital = TotalAssets - CurrentLiabilities
    oi_a = {p["date"]: p["value"] for p in _get("annualOperatingIncome")}
    ta_a = {p["date"]: p["value"] for p in _get("annualTotalAssets")}
    roic_hist = []
    for date in sorted(oi_a.keys()):
        oi = oi_a.get(date)
        ta = ta_a.get(date)
        cl_val = cl_a.get(date)
        if oi is not None and ta is not None and cl_val is not None:
            # 세율 근사: tax_hist에서 가져오거나 기본값 21%
            tax_entry = next((t for t in tax_hist if t["date"] == date), None)
            tax_r = (tax_entry["value"] / 100) if tax_entry else 0.21
            nopat = oi * (1 - tax_r)
            ic = ta - cl_val
            if ic > 0:
                roic_hist.append({"date": date, "value": round(nopat / ic * 100, 2)})
    metrics["roic_hist"] = roic_hist

    # ── 성장률 지표 (연간 YoY) ──
    def _yoy_growth(series_key):
        data_points = _get(series_key)
        if len(data_points) < 2:
            return []
        growth = []
        sorted_pts = sorted(data_points, key=lambda x: x["date"])
        for i in range(1, len(sorted_pts)):
            prev = sorted_pts[i - 1]["value"]
            curr = sorted_pts[i]["value"]
            if prev is not None and curr is not None and prev != 0:
                growth.append({"date": sorted_pts[i]["date"], "value": round((curr - prev) / abs(prev) * 100, 2)})
        return growth

    metrics["eps_growth_hist"] = _yoy_growth("annualDilutedEPS") or _yoy_growth("annualBasicEPS")
    metrics["net_income_growth_hist"] = _yoy_growth("annualNetIncome")
    metrics["operating_income_growth_hist"] = _yoy_growth("annualOperatingIncome")

    # ── 분기별 절대값 지표 ──
    metrics["revenue_quarterly"] = _get("quarterlyTotalRevenue")
    metrics["net_income_quarterly"] = _get("quarterlyNetIncome")
    metrics["operating_income_quarterly"] = _get("quarterlyOperatingIncome")
    metrics["gross_profit_quarterly"] = _get("quarterlyGrossProfit")
    metrics["total_debt_quarterly"] = _get("quarterlyTotalDebt")
    metrics["total_cash_quarterly"] = _get("quarterlyCashAndCashEquivalents")
    metrics["ebitda_quarterly"] = _get("quarterlyEBITDA")
    metrics["fcf_quarterly"] = _get("quarterlyFreeCashFlow")
    metrics["total_assets_quarterly"] = _get("quarterlyTotalAssets")
    metrics["equity_quarterly"] = _get("quarterlyStockholdersEquity")
    metrics["liabilities_quarterly"] = _get("quarterlyTotalLiabilitiesNetMinorityInterest")

    # ── 확장 분기별 절대값 지표 ──
    metrics["pretax_income_quarterly"] = _get("quarterlyPretaxIncome")
    metrics["ebit_quarterly"] = _get("quarterlyEBIT")
    metrics["interest_expense_quarterly"] = _get("quarterlyInterestExpense")
    metrics["accounts_receivable_quarterly"] = _get("quarterlyAccountsReceivable")
    metrics["inventory_quarterly"] = _get("quarterlyInventory")
    metrics["accounts_payable_quarterly"] = _get("quarterlyAccountsPayable")
    metrics["tangible_book_quarterly"] = _get("quarterlyTangibleBookValue")

    # 분기 운전자본
    ca_q = {p["date"]: p["value"] for p in _get("quarterlyCurrentAssets")}
    cl_q = {p["date"]: p["value"] for p in _get("quarterlyCurrentLiabilities")}
    wc_q = []
    for date in sorted(ca_q.keys()):
        ca_val = ca_q.get(date)
        cl_val = cl_q.get(date)
        if ca_val is not None and cl_val is not None:
            wc_q.append({"date": date, "value": round(ca_val - cl_val, 0)})
    metrics["working_capital_quarterly"] = wc_q

    # 분기 BPS
    eq_q_for_bps = {p["date"]: p["value"] for p in _get("quarterlyStockholdersEquity")}
    sh_q_for_bps = {p["date"]: p["value"] for p in _get("quarterlyOrdinarySharesNumber")}
    bps_q = []
    for date in sorted(eq_q_for_bps.keys()):
        eq = eq_q_for_bps.get(date)
        sh = sh_q_for_bps.get(date)
        if eq is not None and sh and sh > 0:
            bps_q.append({"date": date, "value": round(eq / sh, 2)})
    metrics["bps_quarterly"] = bps_q

    # 분기 주당매출
    rev_q = {p["date"]: p["value"] for p in _get("quarterlyTotalRevenue")}
    rps_q = []
    for date in sorted(rev_q.keys()):
        r = rev_q.get(date)
        sh = sh_q_for_bps.get(date)
        if r is not None and sh and sh > 0:
            rps_q.append({"date": date, "value": round(r / sh, 2)})
    metrics["revenue_per_share_quarterly"] = rps_q

    # 분기 주당배당금
    div_paid_q = {p["date"]: abs(p["value"]) for p in _get("quarterlyCashDividendsPaid") if p.get("value")}
    dps_q = []
    for date in sorted(div_paid_q.keys()):
        d = div_paid_q.get(date)
        sh = sh_q_for_bps.get(date)
        if d is not None and sh and sh > 0:
            dps_q.append({"date": date, "value": round(d / sh, 2)})
    metrics["dividend_per_share_quarterly"] = dps_q

    # ── 분기별 비율 지표 ──
    metrics["profit_margin_quarterly"] = _ratio("quarterlyNetIncome", "quarterlyTotalRevenue")
    metrics["operating_margin_quarterly"] = _ratio("quarterlyOperatingIncome", "quarterlyTotalRevenue")
    metrics["gross_margin_quarterly"] = _ratio("quarterlyGrossProfit", "quarterlyTotalRevenue")
    metrics["roe_quarterly"] = _ratio("quarterlyNetIncome", "quarterlyStockholdersEquity")
    metrics["roa_quarterly"] = _ratio("quarterlyNetIncome", "quarterlyTotalAssets")
    metrics["debt_to_equity_quarterly"] = _ratio(
        "quarterlyTotalLiabilitiesNetMinorityInterest", "quarterlyStockholdersEquity", multiply=1
    )

    # ── 확장 분기별 비율 지표 ──
    ni_q = {p["date"]: p["value"] for p in _get("quarterlyNetIncome")}
    pti_q = {p["date"]: p["value"] for p in _get("quarterlyPretaxIncome")}
    tax_q = []
    for date in sorted(pti_q.keys()):
        ni = ni_q.get(date)
        pti = pti_q.get(date)
        if ni is not None and pti is not None and pti != 0:
            tax_q.append({"date": date, "value": round((1 - ni / pti) * 100, 2)})
    metrics["tax_rate_quarterly"] = tax_q

    # 분기 회전율: 분자는 연간과 동일 정의(재고=매출원가). 분모는 분기말 스냅샷
    # (분기 연속 잔액이라 평균의 의미가 약함 — 연간 차트·카드만 평균 사용).
    metrics["asset_turnover_quarterly"] = _ratio("quarterlyTotalRevenue", "quarterlyTotalAssets", multiply=1)
    metrics["inventory_turnover_quarterly"] = _ratio("quarterlyCostOfRevenue", "quarterlyInventory", multiply=1)
    metrics["receivables_turnover_quarterly"] = _ratio("quarterlyTotalRevenue", "quarterlyAccountsReceivable", multiply=1)
    metrics["ocf_margin_quarterly"] = _ratio("quarterlyOperatingCashFlow", "quarterlyTotalRevenue")

    capex_q = {p["date"]: p["value"] for p in _get("quarterlyCapitalExpenditure")}
    capex_ratio_q = []
    for date in sorted(rev_q.keys()):
        cx = capex_q.get(date)
        rv = rev_q.get(date)
        if cx is not None and rv and rv != 0:
            capex_ratio_q.append({"date": date, "value": round(abs(cx) / rv * 100, 2)})
    metrics["capex_to_revenue_quarterly"] = capex_ratio_q

    payout_q = []
    for date in sorted(div_paid_q.keys()):
        d = div_paid_q.get(date)
        ni = ni_q.get(date)
        if d is not None and ni and ni > 0:
            payout_q.append({"date": date, "value": round(d / ni * 100, 2)})
    metrics["payout_ratio_quarterly"] = payout_q

    # 분기 ROIC
    oi_q = {p["date"]: p["value"] for p in _get("quarterlyOperatingIncome")}
    ta_q = {p["date"]: p["value"] for p in _get("quarterlyTotalAssets")}
    roic_q = []
    for date in sorted(oi_q.keys()):
        oi = oi_q.get(date)
        ta = ta_q.get(date)
        cl_val = cl_q.get(date)
        if oi is not None and ta is not None and cl_val is not None:
            tax_entry = next((t for t in tax_q if t["date"] == date), None)
            tax_r = (tax_entry["value"] / 100) if tax_entry else 0.21
            nopat = oi * (1 - tax_r)
            ic = ta - cl_val
            if ic > 0:
                roic_q.append({"date": date, "value": round(nopat / ic * 100, 2)})
    metrics["roic_quarterly"] = roic_q

    # 분기 성장률 (QoQ)
    metrics["eps_growth_quarterly"] = _yoy_growth("quarterlyDilutedEPS") or _yoy_growth("quarterlyBasicEPS")
    metrics["net_income_growth_quarterly"] = _yoy_growth("quarterlyNetIncome")
    metrics["operating_income_growth_quarterly"] = _yoy_growth("quarterlyOperatingIncome")

    # ── 분기별 EPS, PER, PBR ──
    eps_q = _get("quarterlyDilutedEPS") or _get("quarterlyBasicEPS")
    metrics["eps_quarterly"] = eps_q
    equity_q = _get("quarterlyStockholdersEquity")
    shares_q = _get("quarterlyOrdinarySharesNumber")

    q_dates = set()
    for d in eps_q:
        q_dates.add(d["date"])
    for d in equity_q:
        q_dates.add(d["date"])

    if q_dates:
        q_price_map = _get_price_at_dates(ticker, list(q_dates))

        # 분기 PER (TTM 근사: 분기 EPS × 4)
        eps_q_dict = {p["date"]: p["value"] for p in eps_q}
        per_q = []
        for date in sorted(eps_q_dict.keys()):
            eps_val = eps_q_dict.get(date)
            price = q_price_map.get(date)
            if eps_val and price and eps_val > 0:
                per_q.append({"date": date, "value": round(price / (eps_val * 4), 2)})
        metrics["per_quarterly"] = per_q

        # 분기 PBR
        eq_q_dict = {p["date"]: p["value"] for p in equity_q}
        sh_q_dict = {p["date"]: p["value"] for p in shares_q}
        pbr_q = []
        for date in sorted(eq_q_dict.keys()):
            eq = eq_q_dict.get(date)
            sh = sh_q_dict.get(date)
            price = q_price_map.get(date)
            if eq and sh and price and sh > 0:
                bps = eq / sh
                if bps > 0:
                    pbr_q.append({"date": date, "value": round(price / bps, 2)})
        metrics["pbr_quarterly"] = pbr_q

    # ── 시가총액 히스토리 ──
    # API 값 + 직접 계산(주가 × 발행주식수)을 합쳐서 최대한 긴 히스토리 확보
    market_cap_annual_api = {p["date"]: p["value"] for p in _get("annualMarketCap")}
    shares_a = {p["date"]: p["value"] for p in _get("annualOrdinarySharesNumber")}
    mc_hist = {}
    # 1) 직접 계산으로 넓은 범위 확보
    if shares_a:
        all_share_dates = set(shares_a.keys()) | all_dates
        if all_share_dates:
            extended_price_map = _get_price_at_dates(ticker, list(all_share_dates))
            for date in shares_a:
                sh = shares_a.get(date)
                price = extended_price_map.get(date)
                if sh and price:
                    mc_hist[date] = round(sh * price, 0)
    # 2) API 값으로 덮어쓰기 (더 정확)
    mc_hist.update(market_cap_annual_api)
    metrics["market_cap_hist"] = [{"date": d, "value": mc_hist[d]} for d in sorted(mc_hist)]

    market_cap_q_api = {p["date"]: p["value"] for p in _get("quarterlyMarketCap")}
    shares_q_data = {p["date"]: p["value"] for p in _get("quarterlyOrdinarySharesNumber")}
    mc_q_hist = {}
    if shares_q_data:
        all_q_share_dates = set(shares_q_data.keys()) | q_dates
        if all_q_share_dates:
            extended_q_price_map = _get_price_at_dates(ticker, list(all_q_share_dates))
            for date in shares_q_data:
                sh = shares_q_data.get(date)
                price = extended_q_price_map.get(date)
                if sh and price:
                    mc_q_hist[date] = round(sh * price, 0)
    mc_q_hist.update(market_cap_q_api)
    metrics["market_cap_quarterly"] = [{"date": d, "value": mc_q_hist[d]} for d in sorted(mc_q_hist)]

    # ── EV/EBITDA 히스토리 ──
    # EV/EBITDA: API 값 우선, 없으면 직접 계산 (EV = MarketCap + TotalDebt - Cash)
    ev_annual_api = {p["date"]: p["value"] for p in _get("annualEnterpriseValue")}
    ebitda_annual = {p["date"]: p["value"] for p in _get("annualEBITDA")}
    debt_a = {p["date"]: p["value"] for p in _get("annualTotalDebt")}
    cash_a = {p["date"]: p["value"] for p in _get("annualCashAndCashEquivalents")}
    ev_ebitda_hist = []
    all_ebitda_dates = set(ebitda_annual.keys())
    for date in sorted(all_ebitda_dates):
        ebit = ebitda_annual.get(date)
        if not ebit or ebit <= 0:
            continue
        # API EV가 있으면 사용, 없으면 직접 계산
        ev = ev_annual_api.get(date)
        if not ev:
            mc_val = mc_hist.get(date)
            if mc_val:
                ev = mc_val + (debt_a.get(date) or 0) - (cash_a.get(date) or 0)
        if ev:
            ev_ebitda_hist.append({"date": date, "value": round(ev / ebit, 2)})
    metrics["ev_to_ebitda_hist"] = ev_ebitda_hist

    ev_q_api = {p["date"]: p["value"] for p in _get("quarterlyEnterpriseValue")}
    ebitda_q_data = {p["date"]: p["value"] for p in _get("quarterlyEBITDA")}
    debt_q = {p["date"]: p["value"] for p in _get("quarterlyTotalDebt")}
    cash_q_map = {p["date"]: p["value"] for p in _get("quarterlyCashAndCashEquivalents")}
    ev_ebitda_q = []
    for date in sorted(ebitda_q_data.keys()):
        ebit = ebitda_q_data.get(date)
        if not ebit or ebit <= 0:
            continue
        ev = ev_q_api.get(date)
        if not ev:
            mc_val = mc_q_hist.get(date)
            if mc_val:
                ev = mc_val + (debt_q.get(date) or 0) - (cash_q_map.get(date) or 0)
        if ev:
            ev_ebitda_q.append({"date": date, "value": round(ev / (ebit * 4), 2)})
    metrics["ev_to_ebitda_quarterly"] = ev_ebitda_q

    # ── 유동비율 히스토리 ──
    metrics["current_ratio_hist"] = _ratio("annualCurrentAssets", "annualCurrentLiabilities", multiply=1)
    metrics["current_ratio_quarterly"] = _ratio("quarterlyCurrentAssets", "quarterlyCurrentLiabilities", multiply=1)

    # ── 배당수익률 히스토리 ──
    # 연간: |배당지급액| / 시가총액
    dividends_a = _get("annualCashDividendsPaid")
    mc_a_data = metrics.get("market_cap_hist", [])
    if dividends_a and mc_a_data:
        div_dict = {p["date"]: abs(p["value"]) for p in dividends_a}
        mc_dict = {p["date"]: p["value"] for p in mc_a_data}
        div_yield_hist = []
        for date in sorted(div_dict.keys()):
            div = div_dict.get(date)
            mc = mc_dict.get(date)
            if div and mc and mc > 0:
                div_yield_hist.append({"date": date, "value": round(div / mc * 100, 2)})
        metrics["dividend_yield_hist"] = div_yield_hist

    dividends_q = _get("quarterlyCashDividendsPaid")
    mc_q_data = metrics.get("market_cap_quarterly", [])
    if dividends_q and mc_q_data:
        div_q_dict = {p["date"]: abs(p["value"]) for p in dividends_q}
        mc_q_dict = {p["date"]: p["value"] for p in mc_q_data}
        div_yield_q = []
        for date in sorted(div_q_dict.keys()):
            div = div_q_dict.get(date)
            mc = mc_q_dict.get(date)
            if div and mc and mc > 0:
                # 분기 배당금 × 4 / 시가총액 = 연환산 수익률
                div_yield_q.append({"date": date, "value": round(div * 4 / mc * 100, 2)})
        metrics["dividend_yield_quarterly"] = div_yield_q

    # ── SEC 장기 연간 히스토리 병합 (미국 us-gaap filer) ──
    # 절대 달러 지표를 SEC(10년+)로 확장하고, 비율/유도 지표도 병합된 절대값에서 재계산.
    # 외국 filer/미보고 블록은 Yahoo 값 그대로(폴백).
    _merge_sec_annual_history(ticker, metrics, _get)
    _derive_sec_ratios(ticker, metrics, _get)
    _extend_eps_with_sec(ticker, metrics)
    _extend_per_share_with_sec(ticker, metrics)

    return metrics
