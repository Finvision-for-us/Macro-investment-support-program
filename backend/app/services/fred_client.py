import requests
from app.config import FRED_API_KEY

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

INDICATORS = {
    "GDP":        {"series_id": "GDP",       "name": "GDP 성장률", "unit": "십억 달러"},
    "UNRATE":     {"series_id": "UNRATE",    "name": "실업률",    "unit": "%"},
    "CPIAUCSL":   {"series_id": "CPIAUCSL",  "name": "CPI (물가)", "unit": "지수"},
    "DFF":        {"series_id": "DFF",       "name": "기준금리",  "unit": "%"},
    "PCE":        {"series_id": "PCE",       "name": "PCE",       "unit": "십억 달러"},
    "UMCSENT":    {"series_id": "UMCSENT",   "name": "소비자심리지수", "unit": "지수"},
    "INDPRO":     {"series_id": "INDPRO",    "name": "산업생산지수", "unit": "지수"},
    "T10YIE":     {"series_id": "T10YIE",    "name": "기대인플레이션(10년)", "unit": "%"},
    "T10Y2Y":     {"series_id": "T10Y2Y",    "name": "장단기 금리차(10Y-2Y)", "unit": "%"},
    "ICSA":       {"series_id": "ICSA",      "name": "신규 실업수당 청구",   "unit": "천 건"},
    "HOUST":      {"series_id": "HOUST",     "name": "주택착공건수",        "unit": "천 호"},
    "TCU":        {"series_id": "TCU",       "name": "설비가동률",          "unit": "%"},
}

def fetch_series(series_id: str, limit: int = 60):
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY or "none",
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        observations = [
            {"date": o["date"], "value": float(o["value"]) if o["value"] != "." else None}
            for o in reversed(data.get("observations", []))
        ]
        return observations
    except Exception as e:
        return []

def get_latest_value(series_id: str):
    data = fetch_series(series_id, limit=5)
    for item in reversed(data):
        if item["value"] is not None:
            return item["value"], item["date"]
    return None, None

def get_market_state():
    gdp_val, _ = get_latest_value("GDP")
    unrate, _ = get_latest_value("UNRATE")
    cpi_val, _ = get_latest_value("CPIAUCSL")
    fed_rate, _ = get_latest_value("FEDFUNDS")

    # 간이 CPI YoY 계산
    cpi_series = fetch_series("CPIAUCSL", limit=14)
    cpi_yoy = None
    if len(cpi_series) >= 13:
        latest = cpi_series[-1]["value"]
        year_ago = cpi_series[-13]["value"]
        if latest and year_ago:
            cpi_yoy = round((latest - year_ago) / year_ago * 100, 2)

    # GDP QoQ 성장률
    gdp_series = fetch_series("GDP", limit=4)
    gdp_growth = None
    if len(gdp_series) >= 2:
        cur = gdp_series[-1]["value"]
        prev = gdp_series[-2]["value"]
        if cur and prev:
            gdp_growth = round((cur - prev) / prev * 100, 2)

    # 시장 상태 판단
    state = "데이터 부족"
    recommended_sectors = []
    caution_sectors = []

    if gdp_growth is not None and unrate is not None:
        if gdp_growth > 2.0 and unrate < 5.0:
            state = "확장 국면"
            recommended_sectors = ["기술 (XLK)", "임의소비재 (XLY)", "금융 (XLF)"]
            caution_sectors = ["유틸리티 (XLU)", "헬스케어 (XLV)"]
        elif gdp_growth < 0:
            state = "침체 국면"
            recommended_sectors = ["헬스케어 (XLV)", "유틸리티 (XLU)", "필수소비재 (XLP)"]
            caution_sectors = ["자동차", "주택건설", "임의소비재 (XLY)"]
        elif cpi_yoy and cpi_yoy > 4.0:
            state = "고인플레이션 국면"
            recommended_sectors = ["에너지 (XLE)", "원자재 (XLB)", "부동산 (XLRE)"]
            caution_sectors = ["기술 (XLK)", "성장주"]
        else:
            state = "과도기 / 둔화 국면"
            recommended_sectors = ["헬스케어 (XLV)", "필수소비재 (XLP)"]
            caution_sectors = ["소형주", "고베타 성장주"]

    return {
        "state": state,
        "recommended_sectors": recommended_sectors,
        "caution_sectors": caution_sectors,
        "metrics": {
            "gdp_growth_qoq": gdp_growth,
            "unemployment": unrate,
            "cpi_yoy": cpi_yoy,
            "fed_rate": fed_rate,
        }
    }
