import requests
from datetime import date

HEADERS = {"User-Agent": "FinVision personal-research-tool contact@example.com"}

def get_cik(ticker: str):
    url = "https://efts.sec.gov/LATEST/search-index?q=%22{}%22&dateRange=custom&startdt=2020-01-01&forms=10-K".format(ticker)
    # CIK 조회
    try:
        resp = requests.get(
            f"https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker}&type=10-K&dateb=&owner=include&count=1&search_text=&action=getcompany&output=atom",
            headers=HEADERS, timeout=10
        )
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            cik_elem = entry.find(".//atom:CIK", ns)
            if cik_elem is not None:
                return cik_elem.text.zfill(10)
    except:
        pass

    # 대체: company_tickers.json
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS, timeout=10
        )
        tickers = resp.json()
        for _, v in tickers.items():
            if v.get("ticker", "").upper() == ticker.upper():
                return str(v["cik_str"]).zfill(10)
    except:
        pass
    return None

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


def get_annual_concept_series(ticker: str, concepts: list, instant: bool = False,
                              cik: str = None, unit: str = "USD"):
    """SEC XBRL companyconcept에서 '연간' 값 시계열을 반환한다 (미국 us-gaap filer 전용).

    Yahoo timeseries는 최근 ~4년만 주므로, 장기(10년+) 재무 히스토리를 SEC에서 얻기 위한 빌더다.

    concepts: us-gaap 개념 후보/우선순위 리스트. 회계기준 변경으로 시대별 개념이 달라지므로
              '같은 의미의 시대별 대체 개념'만 넘겨 이어붙인다(stitch). 예) 매출 =
              ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"].
              (의미가 다른 개념을 섞으면 연도별 값이 뒤섞이므로 금지)
    instant: 재무상태표(시점) 개념이면 True(예: Assets, StockholdersEquity),
             손익/현금흐름(기간) 개념이면 False(예: Revenues, NetIncomeLoss).
    cik: 이미 알고 있으면 전달(중복 조회 방지). 없으면 get_cik(ticker).
    unit: XBRL 단위 키. 금액은 "USD", 주식수는 "shares", 주당지표(EPS)는 "USD/shares".

    연간 판정: form이 '10-K'(및 정정 '10-K/A')인 사실만 사용한다.
      - 기간(flow) 개념: start~end 길이가 약 1년(330~400일)인 것만.
      - 시점(instant) 개념: 회계연도말 잔액(10-K에 실린 것)만.
    같은 회계연도(end의 연도)가 여러 번 보고되면 가장 최근 filed 값을 채택한다(정정 반영).
    여러 concept의 포인트를 합쳐 연도별 latest-filed로 병합한다.

    반환: [{"fy": 2024, "end": "2024-09-28", "value": 391035000000}, ...] (end 오름차순).
          us-gaap 미제출(외국 filer 등)·조회 실패면 빈 리스트(fail-soft).
    """
    if cik is None:
        cik = get_cik(ticker)
    if not cik:
        return []

    by_fy = {}  # fy(int) -> {"end": str, "value": num, "filed": str}
    for concept in concepts:
        url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            units = resp.json().get("units", {}).get(unit, [])
        except Exception:
            continue
        # 여러 concept의 연도별 값을 latest-filed 기준으로 병합(정정/전환기 처리)
        for fy, d in _reduce_units_to_annual(units, instant).items():
            prev = by_fy.get(fy)
            if prev is None or d["filed"] > prev["filed"]:
                by_fy[fy] = d

    out = [{"fy": fy, "end": d["end"], "value": d["value"]} for fy, d in by_fy.items()]
    out.sort(key=lambda x: x["end"])
    return out


def _reduce_units_to_annual(units, instant):
    """companyconcept USD units를 회계연도별 연간값으로 축약한다 (순수 함수, network 없음).

    - form이 '10-K'(및 정정 '10-K/A')인 것만 사용(연간 보고서).
    - flow(instant=False): start~end 길이가 약 1년(330~400일)인 것만.
    - instant=True: start 없는 시점값만(10-K에 실린 회계연도말 잔액).
    - 같은 회계연도(end의 연도)가 여러 번이면 가장 최근 filed 값 채택(정정 반영).
    반환: dict fy(int) -> {"end": str, "value": num, "filed": str}.
    """
    by_fy = {}
    if not isinstance(units, list):
        return by_fy
    for u in units:
        if not isinstance(u, dict):
            continue
        if not u.get("form", "").startswith("10-K"):
            continue
        end = u.get("end")
        filed = u.get("filed")
        val = u.get("val")
        if not end or not filed or val is None:
            continue
        start = u.get("start")
        if instant:
            if start is not None:
                continue
        else:
            if start is None:
                continue
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            if not (330 <= days <= 400):
                continue
        try:
            fy = int(end[:4])
        except (ValueError, TypeError):
            continue
        prev = by_fy.get(fy)
        if prev is None or filed > prev["filed"]:  # ISO 날짜 문자열 비교 = 최신 filed
            by_fy[fy] = {"end": end, "value": val, "filed": filed}
    return by_fy


# 차트 히스토리용 '기초 building-block' → us-gaap 개념 매핑.
# 개념명은 실제 SEC companyconcept 응답으로 검증한 것만 넣는다(하드코딩·추측 금지).
# 여기 있는 절대값들에서 상위(get_metric_history)가 비율(ROE·마진·회전율 등)을 유도한다.
# 은행 등은 일부 개념(operating_income·inventory·capex…)을 아예 보고하지 않으므로 그 블록은 빈 결과가 되고,
# 상위에서 Yahoo로 폴백한다. (EBITDA·FCF·EBIT·total_debt·tangible_book 등 '유도 필요' 항목은 다음 단계에서 처리)
SEC_CONCEPT_MAP = {
    # ── 손익 (flow, USD) ──
    "revenue": {"concepts": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                             "Revenues", "SalesRevenueNet"], "instant": False, "unit": "USD"},
    "net_income": {"concepts": ["NetIncomeLoss"], "instant": False, "unit": "USD"},
    "operating_income": {"concepts": ["OperatingIncomeLoss"], "instant": False, "unit": "USD"},
    "gross_profit": {"concepts": ["GrossProfit"], "instant": False, "unit": "USD"},
    "pretax_income": {"concepts": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ], "instant": False, "unit": "USD"},
    "interest_expense": {"concepts": ["InterestExpense"], "instant": False, "unit": "USD"},
    # ── 현금흐름 (flow, USD) ──
    "operating_cash_flow": {"concepts": ["NetCashProvidedByUsedInOperatingActivities"],
                            "instant": False, "unit": "USD"},
    # capex: SEC 'Payments…'는 양수(지출액)지만 Yahoo는 음수(현금유출)로 준다 → 병합 부호 정합 위해 음수화.
    "capex": {"concepts": ["PaymentsToAcquirePropertyPlantAndEquipment"], "instant": False,
              "unit": "USD", "negate": True},
    "dividends_paid": {"concepts": ["PaymentsOfDividends"], "instant": False, "unit": "USD"},
    # ── 재무상태 (instant, USD) ──
    "total_assets": {"concepts": ["Assets"], "instant": True, "unit": "USD"},
    "stockholders_equity": {"concepts": ["StockholdersEquity"], "instant": True, "unit": "USD"},
    "total_liabilities": {"concepts": ["Liabilities"], "instant": True, "unit": "USD"},
    "cash": {"concepts": ["CashAndCashEquivalentsAtCarryingValue"], "instant": True, "unit": "USD"},
    "accounts_receivable": {"concepts": ["AccountsReceivableNetCurrent"], "instant": True, "unit": "USD"},
    "inventory": {"concepts": ["InventoryNet"], "instant": True, "unit": "USD"},
    "accounts_payable": {"concepts": ["AccountsPayableCurrent"], "instant": True, "unit": "USD"},
    "current_assets": {"concepts": ["AssetsCurrent"], "instant": True, "unit": "USD"},
    "current_liabilities": {"concepts": ["LiabilitiesCurrent"], "instant": True, "unit": "USD"},
    # ── 주식수(instant, shares) / 주당(flow, USD/shares) ──
    "shares_outstanding": {"concepts": ["CommonStockSharesOutstanding"], "instant": True, "unit": "shares"},
    "eps_diluted": {"concepts": ["EarningsPerShareDiluted"], "instant": False, "unit": "USD/shares"},
}


def get_sec_building_blocks(ticker: str, cik: str = None, blocks: list = None):
    """SEC_CONCEPT_MAP의 기초 building-block들을 SEC에서 연간 시계열로 가져온다.

    반환: {block_key: [{"fy","end","value"}, ...]}. 미보고(은행의 inventory 등)·조회실패 블록은
    빈 리스트로 남긴다(호출측이 Yahoo 폴백). us-gaap 미제출(외국 filer)이면 CIK가 없어 전부 빈 리스트.
    blocks: 특정 블록만 원하면 키 리스트 전달(기본=전체).
    """
    if cik is None:
        cik = get_cik(ticker)
    keys = blocks if blocks else list(SEC_CONCEPT_MAP.keys())
    out = {}
    for key in keys:
        spec = SEC_CONCEPT_MAP.get(key)
        if not spec:
            out[key] = []
            continue
        series = get_annual_concept_series(
            ticker, spec["concepts"], instant=spec["instant"], cik=cik, unit=spec["unit"]
        ) if cik else []
        if spec.get("negate"):  # Yahoo 부호 규약에 맞추기(예: capex 음수)
            series = [{"fy": p["fy"], "end": p["end"], "value": -p["value"]} for p in series]
        out[key] = series
    return out
