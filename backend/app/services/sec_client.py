import logging
import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "FinVision admin@finvision.app",
    "Accept-Encoding": "gzip, deflate",
}

# ── ticker → CIK 매핑 (SEC company_tickers.json, 1회 로드/캐시) ──
# 프로젝트 전체의 단일 CIK 소스. (이전에 earnings_analyzer에 중복 구현이 있었으나 이리로 통합.)
_cik_cache: dict = {}   # {TICKER: 10자리 zero-padded CIK}
_cik_loaded = False


def _load_cik_map():
    """SEC company_tickers.json에서 전체 ticker→CIK 매핑을 1회 로드/캐시한다."""
    global _cik_loaded
    if _cik_loaded:
        return
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        for entry in resp.json().values():
            t = (entry.get("ticker") or "").upper()
            cik = entry.get("cik_str")
            if t and cik:
                _cik_cache[t] = str(cik).zfill(10)
        _cik_loaded = True
        logger.info("SEC CIK map loaded: %d tickers", len(_cik_cache))
    except Exception as e:
        logger.warning("SEC CIK map load failed: %s", e)


def get_cik(ticker: str):
    """ticker → 10자리 zero-padded CIK. 못 찾으면 None. (company_tickers.json 캐시 기반)"""
    if not ticker:
        return None
    _load_cik_map()
    return _cik_cache.get(ticker.upper())

def get_filings(ticker: str, form_types: list = None, limit: int = 20):
    if form_types is None:
        form_types = ["10-K", "10-Q", "8-K", "DEF 14A", "SC 13G"]

    cik = get_cik(ticker)
    if not cik:
        return []

    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        descriptions = recent.get("primaryDocument", [])

        results = []
        for i, form in enumerate(forms):
            if form in form_types:
                acc = accessions[i].replace("-", "")
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{descriptions[i]}"
                results.append({
                    "form": form,
                    "date": dates[i],
                    "accession": accessions[i],
                    "url": filing_url,
                    "index_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=5",
                })
                if len(results) >= limit:
                    break
        return results
    except Exception as e:
        return []
