"""실적 발표 주가 변동 분석 엔진

SEC EDGAR + Yahoo Finance + Finnhub + Alpha Vantage 어닝 서프라이즈 데이터를 결합하여
실적 발표 전후 주가 변동 패턴을 분석하고 시뮬레이션 데이터를 생성.
모든 무료 데이터 소스를 병합하여 최대한 많은 표본을 확보.
"""

import aiosqlite
import asyncio
import json
import re
import requests
import logging
from datetime import datetime, timedelta
from app.database import DB_PATH
from app.services import finnhub_client
from app.services.yfinance_client import _yf_quoteSummary, _yf_chart, HEADERS

logger = logging.getLogger(__name__)

CACHE_DURATION_HOURS = 24 * 7  # 1주 캐시

SEC_HEADERS = {
    "User-Agent": "FinVision admin@finvision.app",
    "Accept-Encoding": "gzip, deflate",
}

# ── SEC EDGAR: ticker → CIK 매핑 (단일 소스 sec_client로 통합) ─────────────────────
from app.services import sec_client


def _get_sec_cik(ticker: str) -> str | None:
    """ticker → 10자리 zero-padded CIK 반환. CIK 소스는 sec_client.get_cik로 통합됨."""
    return sec_client.get_cik(ticker)


def _get_sec_earnings_data(ticker: str) -> list:
    """SEC EDGAR companyconcept API에서 전체 분기별 EPS 히스토리 가져오기.
    frame 필드 기반으로 중복 제거된 데이터를 수집하고,
    Q4는 연간(CYyyyy) - Q1 - Q2 - Q3로 계산.

    Returns: list of dict {period_end, report_date, actual, estimate, surprise_pct, quarter_label}
    """
    cik = _get_sec_cik(ticker)
    if not cik:
        logger.info(f"SEC: CIK not found for {ticker}")
        return []

    for concept in ["EarningsPerShareDiluted", "EarningsPerShareBasic"]:
        url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
        try:
            resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()

            units = data.get("units", {})
            eps_entries = units.get("USD/shares", [])
            if not eps_entries:
                continue

            # frame 기반 매핑: {frame: {val, end, filed, fy, fp}}
            frame_map = {}
            for e in eps_entries:
                frame = e.get("frame")
                if not frame:
                    continue
                val = e.get("val")
                if val is None:
                    continue
                # 같은 frame에 여러 항목이 있을 수 있음 → 가장 최근 filed 사용
                existing = frame_map.get(frame)
                if not existing or e.get("filed", "") > existing.get("filed", ""):
                    frame_map[frame] = {
                        "val": val,
                        "end": e.get("end", ""),
                        "filed": e.get("filed", ""),
                        "fy": e.get("fy"),
                        "fp": e.get("fp"),
                        "form": e.get("form", ""),
                        "frame": frame,
                    }

            # 프레임 분류: CYyyyyQn (분기), CYyyyy (연간)
            quarterly_frames = {}  # {(year, qn): info}
            annual_frames = {}     # {year: info}

            for frame, info in frame_map.items():
                # 분기: CY2024Q1, CY2024Q1I (I suffix 포함 가능)
                m = re.match(r"CY(\d{4})Q(\d)I?$", frame)
                if m:
                    year = int(m.group(1))
                    qn = int(m.group(2))
                    quarterly_frames[(year, qn)] = info
                    continue
                # 연간: CY2024, CY2024I
                m = re.match(r"CY(\d{4})I?$", frame)
                if m:
                    year = int(m.group(1))
                    annual_frames[year] = info

            results = []
            seen_periods = set()

            # 1. 직접 분기 데이터 추가
            for (year, qn), info in sorted(quarterly_frames.items(), reverse=True):
                period_end = info["end"]
                if period_end in seen_periods:
                    continue
                seen_periods.add(period_end)
                results.append({
                    "period_end": period_end,
                    "report_date": info["filed"],
                    "actual": round(info["val"], 4),
                    "estimate": None,
                    "surprise_pct": None,
                    "quarter_label": f"Q{qn} {year}",
                    "source": "SEC",
                })

            # 2. Q4 계산: 연간 - Q1 - Q2 - Q3
            for year, fy_info in sorted(annual_frames.items(), reverse=True):
                q1 = quarterly_frames.get((year, 1))
                q2 = quarterly_frames.get((year, 2))
                q3 = quarterly_frames.get((year, 3))
                if q1 and q2 and q3:
                    q4_val = round(fy_info["val"] - q1["val"] - q2["val"] - q3["val"], 4)
                    period_end = fy_info["end"]
                    if period_end not in seen_periods:
                        seen_periods.add(period_end)
                        results.append({
                            "period_end": period_end,
                            "report_date": fy_info["filed"],
                            "actual": q4_val,
                            "estimate": None,
                            "surprise_pct": None,
                            "quarter_label": f"Q4 {year}",
                            "source": "SEC",
                        })

            # 날짜 역순 정렬
            results.sort(key=lambda x: x["period_end"], reverse=True)
            logger.info(f"SEC EDGAR: {ticker} → {len(results)} quarterly EPS records "
                        f"(direct={len(quarterly_frames)}, Q4_calc={len(results)-len(quarterly_frames)})")
            return results

        except Exception as e:
            logger.warning(f"SEC EDGAR {concept} failed for {ticker}: {e}")
            continue

    return []


# ── SEC EDGAR: 전체 분기별 매출 히스토리 ─────────────────────

def _get_sec_revenue_data(ticker: str) -> list:
    """SEC EDGAR companyconcept API에서 전체 분기별 매출 히스토리 가져오기.
    Revenues 또는 RevenueFromContractWithCustomerExcludingAssessedTax 개념 사용.

    Returns: list of dict {period_end, quarter_label, revenue_actual}
    """
    cik = _get_sec_cik(ticker)
    if not cik:
        return []

    revenue_concepts = [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ]

    for concept in revenue_concepts:
        url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
        try:
            resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()

            units = data.get("units", {})
            usd_entries = units.get("USD", [])
            if not usd_entries:
                continue

            # frame 기반 매핑
            frame_map = {}
            for e in usd_entries:
                frame = e.get("frame")
                if not frame:
                    continue
                val = e.get("val")
                if val is None:
                    continue
                existing = frame_map.get(frame)
                if not existing or e.get("filed", "") > existing.get("filed", ""):
                    frame_map[frame] = {
                        "val": val,
                        "end": e.get("end", ""),
                        "filed": e.get("filed", ""),
                        "frame": frame,
                    }

            # 분기 + 연간 분류
            quarterly_frames = {}
            annual_frames = {}
            for frame, info in frame_map.items():
                m = re.match(r"CY(\d{4})Q(\d)I?$", frame)
                if m:
                    year = int(m.group(1))
                    qn = int(m.group(2))
                    quarterly_frames[(year, qn)] = info
                    continue
                m = re.match(r"CY(\d{4})I?$", frame)
                if m:
                    year = int(m.group(1))
                    annual_frames[year] = info

            results = []
            seen_periods = set()

            # 직접 분기 데이터
            for (year, qn), info in sorted(quarterly_frames.items(), reverse=True):
                period_end = info["end"]
                if period_end in seen_periods:
                    continue
                seen_periods.add(period_end)
                results.append({
                    "period_end": period_end,
                    "quarter_label": f"Q{qn} {year}",
                    "revenue_actual": info["val"],
                })

            # Q4 = 연간 - Q1 - Q2 - Q3
            for year, fy_info in sorted(annual_frames.items(), reverse=True):
                q1 = quarterly_frames.get((year, 1))
                q2 = quarterly_frames.get((year, 2))
                q3 = quarterly_frames.get((year, 3))
                if q1 and q2 and q3:
                    q4_val = fy_info["val"] - q1["val"] - q2["val"] - q3["val"]
                    if q4_val > 0:
                        period_end = fy_info["end"]
                        if period_end not in seen_periods:
                            seen_periods.add(period_end)
                            results.append({
                                "period_end": period_end,
                                "quarter_label": f"Q4 {year}",
                                "revenue_actual": round(q4_val, 2),
                            })

            results.sort(key=lambda x: x["period_end"], reverse=True)
            logger.info(f"SEC Revenue: {ticker} → {len(results)} quarterly records (concept={concept})")
            return results

        except Exception as e:
            logger.warning(f"SEC Revenue {concept} failed for {ticker}: {e}")
            continue

    return []


# ── Alpha Vantage: 전체 분기별 EPS 히스토리 ─────────────────

def _get_alpha_vantage_earnings(ticker: str) -> list:
    """Alpha Vantage EARNINGS 엔드포인트에서 전체 분기 EPS 히스토리 가져오기.
    (API 키가 없으면 빈 리스트 반환)

    Returns: list of dict {period_end, report_date, actual, estimate, surprise_pct, quarter_label}
    """
    try:
        from app.config import ALPHA_VANTAGE_API_KEY
    except ImportError:
        return []

    if not ALPHA_VANTAGE_API_KEY:
        return []

    try:
        url = f"https://www.alphavantage.co/query?function=EARNINGS&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if "Information" in data or "Note" in data:
            logger.warning(f"Alpha Vantage rate limit: {data.get('Information', data.get('Note', ''))[:100]}")
            return []

        quarterly = data.get("quarterlyEarnings", [])
        if not quarterly:
            return []

        results = []
        for q in quarterly:
            period_end = q.get("fiscalDateEnding", "")
            report_date = q.get("reportedDate", "")
            reported_eps = q.get("reportedEPS")
            estimated_eps = q.get("estimatedEPS")
            surprise_pct_str = q.get("surprisePercentage")

            actual = float(reported_eps) if reported_eps and reported_eps != "None" else None
            estimate = float(estimated_eps) if estimated_eps and estimated_eps != "None" else None
            surprise_pct = float(surprise_pct_str) if surprise_pct_str and surprise_pct_str != "None" else None

            # quarter_label 생성
            quarter_label = period_end
            if period_end:
                try:
                    dt = datetime.strptime(period_end, "%Y-%m-%d")
                    qn = (dt.month - 1) // 3 + 1
                    quarter_label = f"Q{qn} {dt.year}"
                except ValueError:
                    pass

            results.append({
                "period_end": period_end,
                "report_date": report_date or None,
                "actual": actual,
                "estimate": estimate,
                "surprise_pct": surprise_pct,
                "quarter_label": quarter_label,
            })

        logger.info(f"Alpha Vantage: {ticker} → {len(results)} quarterly EPS records")
        return results

    except Exception as e:
        logger.warning(f"Alpha Vantage failed for {ticker}: {e}")
        return []


# ── 매출 추정치 & 가이던스 데이터 수집 ─────────────────────

def _get_revenue_estimates(ticker: str) -> dict:
    """Yahoo Finance earningsTrend에서 매출/EPS 추정치 + 성장률 가져오기.
    Returns: {
        current_quarter: {period, end_date, eps_avg, rev_avg, rev_low, rev_high, growth},
        next_quarter: {...},
        current_year: {...},
        next_year: {...},
    }
    """
    data = _yf_quoteSummary(ticker, "earningsTrend")
    if not data:
        return {}

    trend = data.get("earningsTrend", {}).get("trend", [])
    if not trend:
        return {}

    period_map = {"0q": "current_quarter", "+1q": "next_quarter", "0y": "current_year", "+1y": "next_year"}
    result = {}
    for t in trend:
        period = t.get("period", "")
        key = period_map.get(period)
        if not key:
            continue

        ee = t.get("earningsEstimate", {})
        re = t.get("revenueEstimate", {})
        growth = t.get("growth", {})

        def _raw(obj, field):
            v = obj.get(field, {})
            return v.get("raw") if isinstance(v, dict) else None

        result[key] = {
            "period": period,
            "end_date": t.get("endDate", ""),
            "eps_avg": _raw(ee, "avg"),
            "eps_low": _raw(ee, "low"),
            "eps_high": _raw(ee, "high"),
            "eps_growth": _raw(ee, "growth"),
            "rev_avg": _raw(re, "avg"),
            "rev_low": _raw(re, "low"),
            "rev_high": _raw(re, "high"),
            "rev_growth": _raw(re, "growth"),
            "growth": _raw(growth, "") if isinstance(growth, dict) else (growth.get("raw") if isinstance(growth, dict) else None),
            "num_analysts_eps": _raw(ee, "numberOfAnalysts"),
            "num_analysts_rev": _raw(re, "numberOfAnalysts"),
        }
        # growth 필드 처리
        if isinstance(growth, dict) and "raw" in growth:
            result[key]["earnings_growth"] = growth["raw"]
        else:
            result[key]["earnings_growth"] = None

    return result


def _get_revenue_history(ticker: str) -> list:
    """SEC EDGAR + Yahoo Finance에서 전체 분기별 실제 매출 히스토리 가져오기.
    SEC 데이터로 최대한 많은 분기를 확보하고, Yahoo Finance로 최근 4분기 보완.

    Returns: list of {period_end, quarter_label, revenue_actual, earnings_actual}
    """
    results_map = {}  # period_end → data

    # 1단계: SEC EDGAR에서 전체 히스토리 (가장 많은 데이터)
    sec_rev = _get_sec_revenue_data(ticker)
    for item in sec_rev:
        pe = item.get("period_end", "")
        if pe:
            results_map[pe] = {
                "period_end": pe,
                "quarter_label": item.get("quarter_label", pe),
                "revenue_actual": item.get("revenue_actual"),
                "earnings_actual": None,
            }

    # 2단계: Yahoo Finance로 최근 4분기 보완 (earnings_actual 포함)
    data = _yf_quoteSummary(ticker, "earnings,incomeStatementHistoryQuarterly")
    if data:
        # earnings.financialsChart.quarterly
        fc = data.get("earnings", {}).get("financialsChart", {}).get("quarterly", [])
        yf_entries = []
        for q in fc:
            label = q.get("date", "")
            rev_raw = q.get("revenue", {})
            earn_raw = q.get("earnings", {})
            rev = rev_raw.get("raw") if isinstance(rev_raw, dict) else None
            earn = earn_raw.get("raw") if isinstance(earn_raw, dict) else None
            yf_entries.append({"quarter_label": label, "revenue_actual": rev, "earnings_actual": earn})

        # incomeStatementHistoryQuarterly (날짜 매칭)
        ish = data.get("incomeStatementHistoryQuarterly", {}).get("incomeStatementHistory", [])
        for i, stmt in enumerate(ish):
            date_fmt = stmt.get("endDate", {}).get("fmt", "")
            rev = stmt.get("totalRevenue", {}).get("raw")
            if i < len(yf_entries):
                idx = len(yf_entries) - 1 - i
                yf_entries[idx]["period_end"] = date_fmt
                if rev and not yf_entries[idx].get("revenue_actual"):
                    yf_entries[idx]["revenue_actual"] = rev

        # Yahoo 데이터를 SEC 데이터에 병합 (earnings_actual 추가)
        for entry in yf_entries:
            pe = entry.get("period_end", "")
            if not pe:
                continue
            # 날짜 tolerance로 기존 SEC 데이터와 매칭
            match_key = None
            for existing_pe in results_map:
                try:
                    d1 = datetime.strptime(pe, "%Y-%m-%d")
                    d2 = datetime.strptime(existing_pe, "%Y-%m-%d")
                    if abs((d1 - d2).days) <= 10:
                        match_key = existing_pe
                        break
                except ValueError:
                    continue

            if match_key:
                # 기존 항목에 earnings_actual 추가
                if entry.get("earnings_actual") is not None:
                    results_map[match_key]["earnings_actual"] = entry["earnings_actual"]
                if entry.get("revenue_actual") and not results_map[match_key].get("revenue_actual"):
                    results_map[match_key]["revenue_actual"] = entry["revenue_actual"]
            elif pe:
                # 새 항목 추가
                results_map[pe] = {
                    "period_end": pe,
                    "quarter_label": entry.get("quarter_label", pe),
                    "revenue_actual": entry.get("revenue_actual"),
                    "earnings_actual": entry.get("earnings_actual"),
                }

    # 날짜 역순 정렬 (최신 먼저)
    results = sorted(results_map.values(), key=lambda x: x.get("period_end", ""), reverse=True)
    logger.info(f"Revenue history: {ticker} → {len(results)} quarterly records")
    return results


# ── 캐시 관리 ─────────────────────────────────────────────

async def _is_cache_fresh(ticker: str, data_type: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT last_updated FROM cache_metadata WHERE ticker=? AND data_type=?",
                (ticker, data_type)
            )
            row = await cursor.fetchone()
            if not row:
                return False
            last = datetime.fromisoformat(row[0])
            return (datetime.now() - last).total_seconds() < CACHE_DURATION_HOURS * 3600
    except Exception:
        return False


async def _update_cache_timestamp(db, ticker: str, data_type: str):
    await db.execute(
        "INSERT OR REPLACE INTO cache_metadata (ticker, data_type, last_updated) VALUES (?, ?, ?)",
        (ticker, data_type, datetime.now().isoformat())
    )


# ── Yahoo Finance 어닝 데이터 수집 ────────────────────────

def _get_yf_earnings_data(ticker: str) -> list:
    """Yahoo Finance earningsChart에서 분기별 실적 데이터 + 실제 발표일 가져오기.
    Returns: list of dicts with keys: period_end, report_date, actual, estimate, surprise_pct, quarter_label
    """
    data = _yf_quoteSummary(ticker, "earnings,earningsHistory")
    if not data:
        return []

    results = []
    seen_periods = set()

    # 1차: earningsChart.quarterly (발표일 포함)
    earnings = data.get("earnings", {})
    chart_q = earnings.get("earningsChart", {}).get("quarterly", [])
    for q in chart_q:
        period_end = q.get("periodEndDate", {}).get("fmt", "")
        report_date = q.get("reportedDate", {}).get("fmt", "")
        actual_raw = q.get("actual", {})
        estimate_raw = q.get("estimate", {})
        actual = actual_raw.get("raw") if isinstance(actual_raw, dict) else None
        estimate = estimate_raw.get("raw") if isinstance(estimate_raw, dict) else None

        surprise_str = q.get("surprisePct", "")
        try:
            surprise_pct = float(surprise_str) if surprise_str else None
        except (ValueError, TypeError):
            surprise_pct = None

        # surprisePct 직접 계산 (Yahoo 값이 없거나 부정확할 때)
        if surprise_pct is None and actual is not None and estimate is not None and estimate != 0:
            surprise_pct = round((actual - estimate) / abs(estimate) * 100, 4)

        quarter_label = q.get("date", "")  # e.g. "4Q2025"
        if not quarter_label and period_end:
            # 분기 추정
            try:
                dt = datetime.strptime(period_end, "%Y-%m-%d")
                qn = (dt.month - 1) // 3 + 1
                quarter_label = f"Q{qn} {dt.year}"
            except ValueError:
                quarter_label = period_end

        if period_end and period_end not in seen_periods:
            seen_periods.add(period_end)
            results.append({
                "period_end": period_end,
                "report_date": report_date or None,
                "actual": actual,
                "estimate": estimate,
                "surprise_pct": surprise_pct,
                "quarter_label": quarter_label,
            })

    return results


def _get_finnhub_earnings_data(ticker: str) -> list:
    """Finnhub에서 어닝 서프라이즈 데이터 가져오기 (보충용)"""
    surprises = finnhub_client.get_earnings_surprises(ticker)
    if not surprises:
        return []

    results = []
    for s in surprises:
        period = s.get("period", "")
        quarter = s.get("quarter", "")
        year = s.get("year", "")
        quarter_label = f"Q{quarter} {year}" if quarter and year else period

        results.append({
            "period_end": period,
            "report_date": None,  # Finnhub doesn't provide announcement date
            "actual": s.get("actual"),
            "estimate": s.get("estimate"),
            "surprise_pct": s.get("surprisePercent"),
            "quarter_label": quarter_label,
        })
    return results


def _find_matching_key(merged: dict, date_str: str, tolerance_days: int = 10) -> str | None:
    """merged dict에서 date_str과 tolerance_days 이내인 기존 키를 찾기"""
    if not date_str:
        return None
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    for existing_key in merged:
        try:
            existing_dt = datetime.strptime(existing_key, "%Y-%m-%d")
            if abs((target - existing_dt).days) <= tolerance_days:
                return existing_key
        except ValueError:
            continue
    return None


def _merge_earnings_sources(yf_data: list, fh_data: list, sec_data: list = None, av_data: list = None) -> list:
    """SEC EDGAR + Alpha Vantage + Finnhub + Yahoo Finance 데이터 4중 병합.
    우선순위: Yahoo Finance (발표일 포함) > Finnhub (추정치) > Alpha Vantage (전체+추정치) > SEC (전체 히스토리)
    10일 이내 날짜는 같은 분기로 인식하여 중복 제거.

    신뢰도 검증:
    - 각 소스별 estimate를 별도 추적하여 교차검증
    - estimate == actual (서프라이즈 0%)인 오래된 데이터는 미검증 처리
    - 2개 이상 소스에서 estimate가 일치하면 '검증됨'
    """
    merged = {}
    # 소스별 estimate 추적 (교차검증용)
    estimate_by_source = {}  # {period_end_key: {source: estimate_value}}

    def _upsert(item, source_name):
        key = item["period_end"]
        if not key:
            return
        # 소스별 estimate 추적
        if item.get("estimate") is not None:
            match_key_for_track = _find_matching_key(merged, key, tolerance_days=10) or key
            if match_key_for_track not in estimate_by_source:
                estimate_by_source[match_key_for_track] = {}
            estimate_by_source[match_key_for_track][source_name] = item["estimate"]

        # 기존 항목 중 비슷한 날짜 찾기
        match_key = _find_matching_key(merged, key, tolerance_days=10)
        if match_key:
            existing = merged[match_key]
            merged[match_key] = {
                "period_end": match_key,
                "report_date": item.get("report_date") or existing.get("report_date"),
                "actual": item["actual"] if item.get("actual") is not None else existing.get("actual"),
                "estimate": item["estimate"] if item.get("estimate") is not None else existing.get("estimate"),
                "surprise_pct": item["surprise_pct"] if item.get("surprise_pct") is not None else existing.get("surprise_pct"),
                "quarter_label": item.get("quarter_label") or existing.get("quarter_label", match_key),
                "source": source_name,
            }
        else:
            merged[key] = {
                "period_end": key,
                "report_date": item.get("report_date"),
                "actual": item.get("actual"),
                "estimate": item.get("estimate"),
                "surprise_pct": item.get("surprise_pct"),
                "quarter_label": item.get("quarter_label", key),
                "source": source_name,
            }

    # 1단계: SEC 데이터 (base — 가장 많은 표본, 가장 낮은 우선순위)
    if sec_data:
        for item in sec_data:
            _upsert(item, "SEC")

    # 2단계: Alpha Vantage 데이터 (전체 히스토리 + 추정치, 중-하 우선순위)
    if av_data:
        for item in av_data:
            _upsert(item, "AlphaVantage")

    # 3단계: Finnhub 데이터 (추정치 포함, 중간 우선순위)
    for item in fh_data:
        _upsert(item, "Finnhub")

    # 4단계: Yahoo Finance 데이터 (발표일 포함, 최고 우선순위)
    for item in yf_data:
        _upsert(item, "Yahoo")

    # surprise_pct 자동 계산
    for key, item in merged.items():
        if item.get("actual") is not None and item.get("estimate") is not None:
            if item.get("surprise_pct") is None and item["estimate"] != 0:
                item["surprise_pct"] = round(
                    (item["actual"] - item["estimate"]) / abs(item["estimate"]) * 100, 4
                )

    # ── 신뢰도 검증 ──
    verified_count = 0
    unverified_count = 0
    for key, item in merged.items():
        est = item.get("estimate")
        act = item.get("actual")
        sources = estimate_by_source.get(key, {})
        num_est_sources = len(sources)  # estimate를 제공한 소스 수

        # 신뢰도 판단 로직
        if est is None:
            # estimate 자체가 없음
            item["estimate_verified"] = False
            item["estimate_source_count"] = 0
        elif est == act and item.get("surprise_pct") == 0:
            # estimate == actual (서프라이즈 0%) — 의심스러운 데이터
            # 단, 2개 이상 소스에서 동일한 estimate면 실제로 정확히 맞았을 가능성
            if num_est_sources >= 2:
                item["estimate_verified"] = True
                verified_count += 1
            else:
                # 1개 소스만 + est==act → 미검증 (Alpha Vantage가 actual을 복사했을 가능성)
                item["estimate_verified"] = False
                unverified_count += 1
        elif num_est_sources >= 2:
            # 2개 이상 소스에서 estimate 제공 → 교차검증 완료
            item["estimate_verified"] = True
            verified_count += 1
        elif num_est_sources == 1 and est != act:
            # 1개 소스이지만 est ≠ act → 합리적으로 신뢰 가능
            item["estimate_verified"] = True
            verified_count += 1
        else:
            item["estimate_verified"] = False
            unverified_count += 1

        item["estimate_source_count"] = num_est_sources

    # 날짜 역순 정렬
    items = sorted(merged.values(), key=lambda x: x["period_end"], reverse=True)
    logger.info(f"Merged earnings: {len(items)} total "
                f"(SEC={len(sec_data or [])}, AV={len(av_data or [])}, "
                f"Finnhub={len(fh_data)}, YF={len(yf_data)}) | "
                f"Estimates: {verified_count} verified, {unverified_count} unverified")
    return items


async def _fetch_and_cache_earnings(ticker: str):
    """SEC EDGAR + Alpha Vantage + Finnhub + Yahoo Finance 데이터 4중 결합하여 캐싱"""
    import asyncio

    async def _empty():
        return []

    # 모든 blocking HTTP 호출을 thread pool에서 병렬 실행
    sec_task = asyncio.to_thread(_get_sec_earnings_data, ticker)
    av_task = asyncio.to_thread(_get_alpha_vantage_earnings, ticker)
    yf_task = asyncio.to_thread(_get_yf_earnings_data, ticker)
    fh_task = asyncio.to_thread(_get_finnhub_earnings_data, ticker) if finnhub_client.is_available() else _empty()

    sec_data, av_data, yf_data, fh_data = await asyncio.gather(sec_task, av_task, yf_task, fh_task)
    merged = _merge_earnings_sources(yf_data, fh_data, sec_data, av_data)

    if not merged:
        return []

    async with aiosqlite.connect(DB_PATH) as db:
        for m in merged:
            await db.execute("""
                INSERT OR REPLACE INTO earnings_surprises
                (ticker, period_end, report_date, period, eps_estimate, eps_actual, surprise_pct,
                 estimate_verified, estimate_source_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker.upper(),
                m["period_end"],
                m.get("report_date"),
                m["quarter_label"],
                m["estimate"],
                m["actual"],
                m["surprise_pct"],
                1 if m.get("estimate_verified") else 0,
                m.get("estimate_source_count", 0),
            ))
        await _update_cache_timestamp(db, ticker, "earnings_surprises")
        await db.commit()
    return merged


# ── 일봉 주가 데이터 ────────────────────────────────────────

def _get_daily_prices(ticker: str) -> tuple:
    """Yahoo Finance chart API로 전체 히스토리 일봉 가져오기.
    period1=0 & period2=now 방식으로 최대 데이터 확보 (range=max는 월봉으로 다운샘플됨).
    """
    import time as _time

    # period1/period2 방식으로 전체 히스토리 가져오기 (가장 많은 데이터)
    period2 = int(_time.time())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
    params = {"interval": "1d", "period1": 0, "period2": period2}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # Fallback: 10y → 5y
        data = _yf_chart(ticker, interval="1d", range_="10y")
        if not data:
            data = _yf_chart(ticker, interval="1d", range_="5y")

    if not data:
        return {}, []

    try:
        result_data = data["chart"]["result"][0]
        timestamps = result_data["timestamp"]
        ohlcv = result_data["indicators"]["quote"][0]

        price_map = {}
        trading_days = []
        for i, ts in enumerate(timestamps):
            try:
                dt = datetime.fromtimestamp(ts)
                d = dt.strftime("%Y-%m-%d")
                close = ohlcv["close"][i]
                if close is not None:
                    price_map[d] = round(close, 2)
                    trading_days.append(d)
            except (IndexError, TypeError):
                continue

        trading_days = sorted(set(trading_days))
        logger.info(f"Daily prices: {ticker} → {len(price_map)} trading days "
                    f"({trading_days[0] if trading_days else '?'} ~ {trading_days[-1] if trading_days else '?'})")
        return price_map, trading_days
    except (KeyError, IndexError, TypeError):
        return {}, []


# ── 주가 반응 계산 ────────────────────────────────────────

def _compute_price_reactions(ticker: str, earnings_dates: list) -> list:
    """각 실적발표일에 대해 주가 변동률 계산"""
    price_map, trading_days = _get_daily_prices(ticker)
    if not price_map:
        return []

    def _find_trading_day_offset(base_date_str, offset):
        """base_date에서 offset 거래일만큼 이동한 날짜"""
        try:
            base_idx = None
            base_date = datetime.strptime(base_date_str, "%Y-%m-%d")

            if base_date_str in price_map:
                base_idx = trading_days.index(base_date_str)
            else:
                # 가장 가까운 거래일 찾기
                for delta in range(1, 6):
                    for d in [1, -1]:
                        check = (base_date + timedelta(days=delta * d)).strftime("%Y-%m-%d")
                        if check in price_map:
                            base_idx = trading_days.index(check)
                            break
                    if base_idx is not None:
                        break

            if base_idx is None:
                return None

            target_idx = base_idx + offset
            if 0 <= target_idx < len(trading_days):
                return trading_days[target_idx]
        except (ValueError, IndexError):
            pass
        return None

    def _pct_change(from_date, to_date):
        if from_date and to_date:
            p0 = price_map.get(from_date)
            p1 = price_map.get(to_date)
            if p0 and p1 and p0 != 0:
                return round((p1 - p0) / p0 * 100, 2)
        return None

    reactions = []
    for ed in earnings_dates:
        if not ed:
            continue
        d_minus_3 = _find_trading_day_offset(ed, -3)
        d_minus_1 = _find_trading_day_offset(ed, -1)
        d_0 = _find_trading_day_offset(ed, 0)
        d_plus_1 = _find_trading_day_offset(ed, 1)
        d_plus_3 = _find_trading_day_offset(ed, 3)
        d_plus_5 = _find_trading_day_offset(ed, 5)

        reactions.append({
            "earnings_date": ed,
            "close_on_date": price_map.get(d_0) if d_0 else None,
            "pre_3d_change": _pct_change(d_minus_3, d_minus_1),
            "reaction_1d_change": _pct_change(d_minus_1, d_plus_1),
            "post_3d_change": _pct_change(d_0, d_plus_3),
            "post_5d_change": _pct_change(d_0, d_plus_5),
        })
    return reactions


async def _cache_price_reactions(ticker: str, reactions: list):
    """주가 반응 데이터를 SQLite에 캐싱"""
    async with aiosqlite.connect(DB_PATH) as db:
        for r in reactions:
            await db.execute("""
                INSERT OR REPLACE INTO earnings_price_reactions
                (ticker, earnings_date, pre_3d_change, reaction_1d_change, post_3d_change, post_5d_change, close_on_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker.upper(), r["earnings_date"],
                r["pre_3d_change"], r["reaction_1d_change"],
                r["post_3d_change"], r["post_5d_change"],
                r["close_on_date"]
            ))
        await _update_cache_timestamp(db, ticker, "price_reactions")
        await db.commit()


# ── 분류 & 통계 ──────────────────────────────────────────

def _classify(surprise_pct):
    """Beat/Meet/Miss 분류"""
    if surprise_pct is None:
        return "Unknown"
    if surprise_pct > 2.0:
        return "Beat"
    elif surprise_pct < -2.0:
        return "Miss"
    return "Meet"


def _compute_statistics(history: list) -> dict:
    """카테고리별 통계 생성"""
    categories = {"Beat": [], "Meet": [], "Miss": []}
    for item in history:
        cat = item.get("category", "Unknown")
        if cat in categories:
            categories[cat].append(item)

    stats = {}
    for cat, items in categories.items():
        if not items:
            stats[cat] = {"count": 0}
            continue

        reaction_vals = [i["reaction_1d_change"] for i in items if i.get("reaction_1d_change") is not None]
        post_3d_vals = [i["post_3d_change"] for i in items if i.get("post_3d_change") is not None]
        post_5d_vals = [i["post_5d_change"] for i in items if i.get("post_5d_change") is not None]
        pre_3d_vals = [i["pre_3d_change"] for i in items if i.get("pre_3d_change") is not None]

        def _avg(lst): return round(sum(lst) / len(lst), 2) if lst else None
        def _med(lst):
            if not lst: return None
            s = sorted(lst)
            n = len(s)
            return round(s[n // 2], 2) if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 2)

        up_count = len([v for v in reaction_vals if v > 0])
        up_prob = round(up_count / len(reaction_vals) * 100, 1) if reaction_vals else None

        stats[cat] = {
            "count": len(items),
            "avg_reaction_1d": _avg(reaction_vals),
            "median_reaction_1d": _med(reaction_vals),
            "avg_post_3d": _avg(post_3d_vals),
            "avg_post_5d": _avg(post_5d_vals),
            "avg_pre_3d": _avg(pre_3d_vals),
            "min_reaction": round(min(reaction_vals), 2) if reaction_vals else None,
            "max_reaction": round(max(reaction_vals), 2) if reaction_vals else None,
            "up_probability": up_prob,
        }
    return stats


# ── 피어슨 상관계수 헬퍼 ─────────────────────────────────

def _pearson_correlation(pairs: list) -> tuple:
    """Compute Pearson correlation and R² from list of (x, y) pairs.
    Returns (correlation, r_squared). Returns (0, 0) if insufficient data."""
    n = len(pairs)
    if n < 3:
        return (0.0, 0.0)
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / n
    std_x = (sum((x - mean_x) ** 2 for x in xs) / n) ** 0.5
    std_y = (sum((y - mean_y) ** 2 for y in ys) / n) ** 0.5
    if std_x > 0 and std_y > 0:
        corr = round(cov / (std_x * std_y), 4)
        r_sq = round(corr ** 2, 4)
    else:
        corr = 0.0
        r_sq = 0.0
    return (corr, r_sq)


# ── 섹터별 팩터 컨텍스트 ─────────────────────────────────
SECTOR_CONTEXT = {
    "Technology": {
        "type": "기술주",
        "desc": "기술 섹터는 매출 성장과 수익성 개선이 핵심입니다.",
        "notes": {
            "eps_surprise": "수익성 개선은 밸류에이션 프리미엄에 직접 영향을 미칩니다.",
            "revenue_growth": "TAM(전체 시장 규모) 대비 성장 속도가 핵심 지표입니다.",
            "margin_trend": "규모의 경제 달성 여부를 나타내는 중요한 신호입니다.",
            "revenue_acceleration": "성장 가속/감속은 시장 모멘텀의 핵심 신호입니다.",
        }
    },
    "Consumer Cyclical": {
        "type": "경기민감 소비주",
        "desc": "소비 트렌드와 마진 관리가 핵심입니다.",
        "notes": {
            "eps_surprise": "소비 수요 변화에 따른 수익 변동을 반영합니다.",
            "revenue_growth": "소비자 수요 변화를 직접 반영하는 핵심 지표입니다.",
            "margin_trend": "원가 관리와 가격 전가 능력이 수익에 직접 영향합니다.",
            "revenue_acceleration": "소비 트렌드 가속/감속을 나타냅니다.",
        }
    },
    "Consumer Defensive": {
        "type": "필수소비재/배당주",
        "desc": "안정적 수익과 배당 지속성이 가장 중요합니다.",
        "notes": {
            "eps_surprise": "안정적 수익 흐름이 배당 지속의 근거이므로 핵심 지표입니다.",
            "revenue_growth": "성숙 시장에서의 점유율 변화를 반영합니다.",
            "margin_trend": "원자재 가격 변동 대응력의 척도입니다.",
            "revenue_acceleration": "수요 안정성 변화를 측정합니다.",
        }
    },
    "Healthcare": {
        "type": "헬스케어",
        "desc": "신약 승인, 파이프라인, 매출 성장이 핵심입니다.",
        "notes": {
            "eps_surprise": "R&D 투자 대비 수익화 여부를 보여줍니다.",
            "revenue_growth": "신약/제품 매출 성장이 파이프라인 가치를 검증합니다.",
            "margin_trend": "약가 정책과 제네릭 경쟁 영향을 반영합니다.",
            "revenue_acceleration": "블록버스터 약물의 시장 침투 속도를 나타냅니다.",
        }
    },
    "Financial Services": {
        "type": "금융주",
        "desc": "순이자마진, 대출 성장, 자산 건전성이 핵심입니다.",
        "notes": {
            "eps_surprise": "금리 환경에 따른 순이자마진 변화를 반영합니다.",
            "revenue_growth": "대출/수수료 수입 성장을 나타냅니다.",
            "margin_trend": "금리 스프레드와 비용 효율성의 척도입니다.",
            "revenue_acceleration": "경기 사이클에 따른 금융 수요 변화를 반영합니다.",
        }
    },
    "Communication Services": {
        "type": "커뮤니케이션/미디어",
        "desc": "사용자 성장, 광고 매출, 콘텐츠 투자가 핵심입니다.",
        "notes": {
            "eps_surprise": "광고 수익화 효율과 비용 관리를 반영합니다.",
            "revenue_growth": "사용자 기반과 ARPU(인당 매출) 성장을 나타냅니다.",
            "margin_trend": "콘텐츠 투자 대비 수익화 효율을 보여줍니다.",
            "revenue_acceleration": "광고 시장 점유율 변화를 나타냅니다.",
        }
    },
    "Industrials": {
        "type": "산업재",
        "desc": "수주 잔고, 매출 성장, 마진 관리가 핵심입니다.",
        "notes": {
            "eps_surprise": "원가 관리와 운영 효율성을 반영합니다.",
            "revenue_growth": "수주 잔고와 산업 수요 트렌드를 나타냅니다.",
            "margin_trend": "원자재 가격과 노동비용 관리 능력을 반영합니다.",
            "revenue_acceleration": "경기 사이클에 따른 산업 수요 변화를 측정합니다.",
        }
    },
    "Energy": {
        "type": "에너지",
        "desc": "유가/가스 가격, 생산량, 자본 배분이 핵심입니다.",
        "notes": {
            "eps_surprise": "원자재 가격 변동과 헤지 전략의 결과를 반영합니다.",
            "revenue_growth": "생산량과 에너지 가격 변화를 직접 반영합니다.",
            "margin_trend": "생산 비용 관리와 유가 민감도를 나타냅니다.",
            "revenue_acceleration": "에너지 수요와 가격 트렌드 변화를 측정합니다.",
        }
    },
    "Real Estate": {
        "type": "부동산/리츠",
        "desc": "FFO, 입주율, 임대 성장률이 핵심입니다.",
        "notes": {
            "eps_surprise": "FFO(운영현금흐름) 기반으로 해석해야 합니다. 전통 EPS는 참고용입니다.",
            "revenue_growth": "임대 수입 성장과 입주율 변화를 반영합니다.",
            "margin_trend": "운영 비용 효율성과 금리 영향을 나타냅니다.",
            "revenue_acceleration": "부동산 시장 사이클 변화를 측정합니다.",
        }
    },
    "Basic Materials": {
        "type": "소재/원자재",
        "desc": "원자재 가격, 생산량, 비용 관리가 핵심입니다.",
        "notes": {
            "eps_surprise": "원자재 가격 사이클과 비용 관리 결과를 반영합니다.",
            "revenue_growth": "원자재 가격과 수요 변화를 직접 반영합니다.",
            "margin_trend": "채굴/생산 비용 대비 판매 가격 스프레드입니다.",
            "revenue_acceleration": "글로벌 산업 수요 변화를 나타냅니다.",
        }
    },
    "Utilities": {
        "type": "유틸리티/공공",
        "desc": "규제 요율, 배당 안정성, 설비 투자가 핵심입니다.",
        "notes": {
            "eps_surprise": "규제 환경과 요율 승인 결과를 반영합니다.",
            "revenue_growth": "고객 성장과 요율 변화를 나타냅니다.",
            "margin_trend": "연료비와 운영 효율성을 반영합니다.",
            "revenue_acceleration": "에너지 수요와 신규 투자 효과를 측정합니다.",
        }
    },
}


# ── 종목별 다중 요인 프로필 분석 ─────────────────────────

def _compute_multi_factor_profile(history: list, revenue_history: list = None,
                                   sector: str = None, guidance_data: list = None) -> dict:
    """종목별 다중 요인 분석 - 어떤 요인이 주가에 가장 큰 영향을 미치는지 자동 파악.

    Returns: {
        "factors": [...],
        "top_factors": [...],
        "guidance_analysis": {...},
        "simulation_config": {...},
        "predictability_score": float (0-100),
        "avg_volatility": float,
        "recent_trend": str,
        "data_quality": str,
        "verified_sample_size": int,
        "profile_message": str,
        # backward compatibility
        "eps_correlation": float,
        "eps_r_squared": float,
        "eps_reliability": str,
        "dominant_factor": str,
        "guidance_impact": float,
        "direction_match_pct": float,
    }
    """
    if revenue_history is None:
        revenue_history = []

    # 검증된 데이터만 사용
    verified = [h for h in history
                if h.get("estimate_verified")
                and h.get("surprise_pct") is not None
                and h.get("reaction_1d_change") is not None]

    n = len(verified)

    # ── 기본 결과 (데이터 부족 시) ──
    empty_guidance = {
        "beat_but_down_count": 0,
        "beat_but_down_pct": 0.0,
        "miss_but_up_count": 0,
        "miss_but_up_pct": 0.0,
        "guidance_influence_score": 0.0,
        "cases": [],
        "message": "데이터 부족",
    }
    result = {
        "factors": [],
        "top_factors": [],
        "guidance_analysis": empty_guidance,
        "simulation_config": {
            "primary_factor": None,
            "input_factors": [],
            "chart_type": "eps",
        },
        "predictability_score": 0,
        "avg_volatility": None,
        "recent_trend": None,
        "data_quality": "insufficient",
        "verified_sample_size": n,
        "profile_message": "검증된 데이터가 부족하여 종목 프로필을 생성할 수 없습니다.",
        # backward compat
        "eps_correlation": None,
        "eps_r_squared": None,
        "eps_reliability": "insufficient",
        "dominant_factor": "데이터 부족",
        "guidance_impact": None,
        "direction_match_pct": None,
    }

    if n < 5:
        return result

    # ══════════════════════════════════════════════════════════
    # 1. EPS 서프라이즈 ↔ 주가 반응
    # ══════════════════════════════════════════════════════════
    eps_pairs = [(h["surprise_pct"], h["reaction_1d_change"]) for h in verified]
    eps_corr, eps_r_sq = _pearson_correlation(eps_pairs)
    reactions = [h["reaction_1d_change"] for h in verified]

    # 방향 일치율
    direction_match_count = sum(
        1 for s, r in eps_pairs if (s > 0 and r > 0) or (s < 0 and r < 0)
    )
    direction_pct = round(direction_match_count / n * 100, 1)

    # 평균 변동성
    abs_reactions = [abs(r) for r in reactions]
    avg_vol = round(sum(abs_reactions) / n, 2)

    # EPS factor entry
    eps_factor = {
        "id": "eps_surprise",
        "name": "EPS 서프라이즈",
        "name_en": "EPS Surprise",
        "correlation": eps_corr,
        "r_squared": eps_r_sq,
        "weight": 0.0,
        "direction": "positive" if eps_corr >= 0 else "negative",
        "reliability": "high" if eps_r_sq >= 0.3 else ("medium" if eps_r_sq >= 0.1 else "low"),
        "sample_size": n,
        "description": f"EPS 서프라이즈 vs 주가 반응 상관계수 {eps_corr:.3f} (R²={eps_r_sq:.3f}, n={n})",
    }
    all_factors = [eps_factor]

    # ══════════════════════════════════════════════════════════
    # 2. 매출 데이터 매칭 (period_end ±15일 tolerance)
    # ══════════════════════════════════════════════════════════
    rev_by_period = {}  # period_end_str → revenue_actual
    if revenue_history:
        for rv in revenue_history:
            pe = rv.get("period_end") or rv.get("date") or rv.get("period")
            rev_val = rv.get("revenue_actual") or rv.get("revenue") or rv.get("totalRevenue") or rv.get("actual")
            if pe and rev_val is not None:
                try:
                    rev_val = float(rev_val)
                except (ValueError, TypeError):
                    continue
                rev_by_period[pe] = rev_val

    def _parse_date_safe(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt)
            except (ValueError, TypeError):
                continue
        return None

    # Build matched list: each verified entry + matched revenue
    matched_entries = []
    for h in verified:
        pe_str = h.get("period_end", "")
        pe_date = _parse_date_safe(pe_str)
        matched_rev = None

        if pe_str in rev_by_period:
            matched_rev = rev_by_period[pe_str]
        elif pe_date:
            # ±15 day tolerance
            for rp_str, rp_val in rev_by_period.items():
                rp_date = _parse_date_safe(rp_str)
                if rp_date and abs((pe_date - rp_date).days) <= 15:
                    matched_rev = rp_val
                    break

        matched_entries.append({
            **h,
            "_revenue_actual": matched_rev,
            "_period_end_date": pe_date,
        })

    # Sort by period_end ascending for YoY computation
    matched_entries_asc = sorted(
        [m for m in matched_entries if m["_period_end_date"]],
        key=lambda m: m["_period_end_date"],
    )

    # ══════════════════════════════════════════════════════════
    # 3. Revenue Growth YoY 계산
    # ══════════════════════════════════════════════════════════
    # For each entry, find same-quarter ~1 year ago (330-400 day gap)
    for i, entry in enumerate(matched_entries_asc):
        entry["_revenue_growth_yoy"] = None
        if entry["_revenue_actual"] is None:
            continue
        cur_date = entry["_period_end_date"]
        cur_rev = entry["_revenue_actual"]
        for j in range(i - 1, -1, -1):
            prev = matched_entries_asc[j]
            if prev["_revenue_actual"] is None:
                continue
            prev_date = prev["_period_end_date"]
            gap_days = (cur_date - prev_date).days
            if 330 <= gap_days <= 400:
                prev_rev = prev["_revenue_actual"]
                if abs(prev_rev) > 0:
                    entry["_revenue_growth_yoy"] = round(
                        (cur_rev - prev_rev) / abs(prev_rev) * 100, 2
                    )
                break

    # Revenue Growth YoY factor
    rev_yoy_pairs = [
        (m["_revenue_growth_yoy"], m["reaction_1d_change"])
        for m in matched_entries_asc
        if m["_revenue_growth_yoy"] is not None and m.get("reaction_1d_change") is not None
    ]
    rev_yoy_corr, rev_yoy_r_sq = _pearson_correlation(rev_yoy_pairs)

    rev_growth_factor = {
        "id": "revenue_growth",
        "name": "매출 성장률(YoY)",
        "name_en": "Revenue Growth YoY",
        "correlation": rev_yoy_corr,
        "r_squared": rev_yoy_r_sq,
        "weight": 0.0,
        "direction": "positive" if rev_yoy_corr >= 0 else "negative",
        "reliability": "high" if rev_yoy_r_sq >= 0.3 else ("medium" if rev_yoy_r_sq >= 0.1 else "low"),
        "sample_size": len(rev_yoy_pairs),
        "description": f"매출 YoY 성장률 vs 주가 반응 (R²={rev_yoy_r_sq:.3f}, n={len(rev_yoy_pairs)})",
    }
    all_factors.append(rev_growth_factor)

    # ══════════════════════════════════════════════════════════
    # 4. Revenue Acceleration (YoY 성장률 변화)
    # ══════════════════════════════════════════════════════════
    # For each entry with YoY, find previous quarter's YoY (60-120 day gap)
    for i, entry in enumerate(matched_entries_asc):
        entry["_revenue_acceleration"] = None
        if entry["_revenue_growth_yoy"] is None:
            continue
        cur_date = entry["_period_end_date"]
        for j in range(i - 1, -1, -1):
            prev = matched_entries_asc[j]
            if prev["_revenue_growth_yoy"] is None:
                continue
            prev_date = prev["_period_end_date"]
            gap_days = (cur_date - prev_date).days
            if 60 <= gap_days <= 120:
                entry["_revenue_acceleration"] = round(
                    entry["_revenue_growth_yoy"] - prev["_revenue_growth_yoy"], 2
                )
                break

    rev_accel_pairs = [
        (m["_revenue_acceleration"], m["reaction_1d_change"])
        for m in matched_entries_asc
        if m["_revenue_acceleration"] is not None and m.get("reaction_1d_change") is not None
    ]
    rev_accel_corr, rev_accel_r_sq = _pearson_correlation(rev_accel_pairs)

    rev_accel_factor = {
        "id": "revenue_acceleration",
        "name": "매출 성장 가속도",
        "name_en": "Revenue Acceleration",
        "correlation": rev_accel_corr,
        "r_squared": rev_accel_r_sq,
        "weight": 0.0,
        "direction": "positive" if rev_accel_corr >= 0 else "negative",
        "reliability": "high" if rev_accel_r_sq >= 0.3 else ("medium" if rev_accel_r_sq >= 0.1 else "low"),
        "sample_size": len(rev_accel_pairs),
        "description": f"매출 YoY 성장 가속도 vs 주가 반응 (R²={rev_accel_r_sq:.3f}, n={len(rev_accel_pairs)})",
    }
    all_factors.append(rev_accel_factor)

    # ══════════════════════════════════════════════════════════
    # 4-b. Margin Trend (수익성 변화) - earnings/revenue ratio 변화
    # ══════════════════════════════════════════════════════════
    # revenue_history에서 earnings_actual과 revenue_actual로 마진 계산
    margin_pairs_data = []
    if revenue_history and len(revenue_history) >= 4:
        # earnings_actual과 revenue_actual이 모두 있는 항목 정렬
        rev_with_margin = []
        for rv in revenue_history:
            pe = rv.get("period_end")
            rev_val = rv.get("revenue_actual")
            earn_val = rv.get("earnings_actual")
            if pe and rev_val and earn_val and abs(rev_val) > 0:
                rev_with_margin.append({
                    "period_end": pe,
                    "margin": earn_val / rev_val,
                })
        rev_with_margin.sort(key=lambda x: x["period_end"])

        # QoQ margin change 계산
        for i in range(1, len(rev_with_margin)):
            curr = rev_with_margin[i]
            prev = rev_with_margin[i - 1]
            margin_change_pp = (curr["margin"] - prev["margin"]) * 100  # percentage points

            # 해당 분기의 주가 반응과 매칭
            pe_str = curr["period_end"]
            pe_date = _parse_date_safe(pe_str)
            if not pe_date:
                continue
            matching_h = None
            for h in verified:
                h_pe = _parse_date_safe(h.get("period_end", ""))
                if h_pe and abs((pe_date - h_pe).days) <= 15:
                    matching_h = h
                    break
            if matching_h and matching_h.get("reaction_1d_change") is not None:
                margin_pairs_data.append((margin_change_pp, matching_h["reaction_1d_change"]))

    margin_corr, margin_r_sq = _pearson_correlation(margin_pairs_data) if len(margin_pairs_data) >= 5 else (0.0, 0.0)

    if len(margin_pairs_data) >= 5:
        margin_factor = {
            "id": "margin_trend",
            "name": "수익성 변화(마진)",
            "name_en": "Margin Trend",
            "correlation": margin_corr,
            "r_squared": margin_r_sq,
            "weight": 0.0,
            "direction": "positive" if margin_corr >= 0 else "negative",
            "reliability": "high" if margin_r_sq >= 0.3 else ("medium" if margin_r_sq >= 0.1 else "low"),
            "sample_size": len(margin_pairs_data),
            "description": f"순이익 마진 QoQ 변화 vs 주가 반응 (R²={margin_r_sq:.3f}, n={len(margin_pairs_data)})",
        }
        all_factors.append(margin_factor)

    # ══════════════════════════════════════════════════════════
    # 4-c. Guidance Sentiment (가이던스 감성) — AI 트랜스크립트 분석 결과 활용
    # ══════════════════════════════════════════════════════════
    if guidance_data:
        # guidance_data: [{period_end, sentiment_score, key_themes, ...}]
        # 각 분기의 sentiment_score ↔ 주가 반응 상관관계 계산
        guidance_by_pe = {}
        for g in guidance_data:
            pe = g.get("period_end")
            sent = g.get("sentiment_score")
            if pe and sent is not None:
                guidance_by_pe[pe] = sent

        guidance_pairs = []
        for h in verified:
            pe_str = h.get("period_end", "")
            pe_date = _parse_date_safe(pe_str)
            reaction = h.get("reaction_1d_change")
            if reaction is None:
                continue

            # period_end 정확 매칭
            sent = guidance_by_pe.get(pe_str)
            # ±15일 tolerance
            if sent is None and pe_date:
                for g_pe, g_sent in guidance_by_pe.items():
                    g_date = _parse_date_safe(g_pe)
                    if g_date and abs((pe_date - g_date).days) <= 15:
                        sent = g_sent
                        break

            if sent is not None:
                # sentiment 50=중립, 0~100 → 정규화: (sent - 50) = -50 ~ +50
                guidance_pairs.append((sent - 50, reaction))

        if len(guidance_pairs) >= 5:
            guidance_corr, guidance_r_sq = _pearson_correlation(guidance_pairs)
            guidance_factor = {
                "id": "guidance_sentiment",
                "name": "가이던스 감성(AI분석)",
                "name_en": "Guidance Sentiment",
                "correlation": guidance_corr,
                "r_squared": guidance_r_sq,
                "weight": 0.0,
                "direction": "positive" if guidance_corr >= 0 else "negative",
                "reliability": "high" if guidance_r_sq >= 0.3 else ("medium" if guidance_r_sq >= 0.1 else "low"),
                "sample_size": len(guidance_pairs),
                "description": f"AI 트랜스크립트 감성점수 vs 주가 반응 (R²={guidance_r_sq:.3f}, n={len(guidance_pairs)})",
            }
            all_factors.append(guidance_factor)

    # ══════════════════════════════════════════════════════════
    # 5. 요인 가중치 정규화
    # ══════════════════════════════════════════════════════════
    total_abs_corr = sum(abs(f["correlation"]) for f in all_factors)
    if total_abs_corr > 0:
        for f in all_factors:
            f["weight"] = round(abs(f["correlation"]) / total_abs_corr, 4)
    elif all_factors:
        equal_w = round(1.0 / len(all_factors), 4)
        for f in all_factors:
            f["weight"] = equal_w

    # Sort by |correlation| descending for top_factors
    sorted_factors = sorted(all_factors, key=lambda f: abs(f["correlation"]), reverse=True)
    top_factors = sorted_factors[:3]

    # ══════════════════════════════════════════════════════════
    # 6. Guidance Impact Analysis
    # ══════════════════════════════════════════════════════════
    beat_but_down_cases = []
    miss_but_up_cases = []
    beat_total = 0
    miss_total = 0

    for h in verified:
        cat = h.get("category")
        reaction = h.get("reaction_1d_change", 0)
        surprise = h.get("surprise_pct", 0)
        if cat == "Beat":
            beat_total += 1
            if reaction < 0:
                beat_but_down_cases.append({
                    "date": h.get("date", ""),
                    "period": h.get("period", ""),
                    "eps_category": "Beat",
                    "surprise_pct": round(surprise, 2) if surprise else 0,
                    "reaction": round(reaction, 2),
                    "interpretation": f"EPS Beat({surprise:+.1f}%)했지만 주가 {reaction:+.1f}% → 가이던스/기대치 실망 가능성",
                })
        elif cat == "Miss":
            miss_total += 1
            if reaction > 0:
                miss_but_up_cases.append({
                    "date": h.get("date", ""),
                    "period": h.get("period", ""),
                    "eps_category": "Miss",
                    "surprise_pct": round(surprise, 2) if surprise else 0,
                    "reaction": round(reaction, 2),
                    "interpretation": f"EPS Miss({surprise:+.1f}%)했지만 주가 +{reaction:.1f}% → 가이던스/기대 상향 또는 선반영",
                })

    beat_but_down_count = len(beat_but_down_cases)
    miss_but_up_count = len(miss_but_up_cases)
    beat_but_down_pct = round(beat_but_down_count / beat_total * 100, 1) if beat_total >= 1 else 0.0
    miss_but_up_pct = round(miss_but_up_count / miss_total * 100, 1) if miss_total >= 1 else 0.0

    # guidance_influence_score: higher = EPS alone doesn't predict well
    total_classified = beat_total + miss_total
    mismatch_count = beat_but_down_count + miss_but_up_count
    guidance_influence_score = round(mismatch_count / total_classified * 100, 1) if total_classified >= 3 else 0.0

    all_guidance_cases = sorted(
        beat_but_down_cases + miss_but_up_cases,
        key=lambda c: c["date"],
        reverse=True,
    )

    if guidance_influence_score >= 40:
        guidance_msg = f"EPS 결과와 주가 방향이 불일치한 비율 {guidance_influence_score}% — 가이던스/매출 등 비EPS 요인이 매우 중요한 종목입니다."
    elif guidance_influence_score >= 20:
        guidance_msg = f"EPS 외 요인 영향도 {guidance_influence_score}% — 가이던스가 종종 주가 방향을 결정합니다."
    else:
        guidance_msg = f"EPS 결과가 주가 방향과 대체로 일치합니다 (불일치 {guidance_influence_score}%)."

    guidance_analysis = {
        "beat_but_down_count": beat_but_down_count,
        "beat_but_down_pct": beat_but_down_pct,
        "miss_but_up_count": miss_but_up_count,
        "miss_but_up_pct": miss_but_up_pct,
        "guidance_influence_score": guidance_influence_score,
        "cases": all_guidance_cases[:10],  # 최대 10건
        "message": guidance_msg,
    }

    # ══════════════════════════════════════════════════════════
    # 7. 데이터 품질
    # ══════════════════════════════════════════════════════════
    if n >= 30:
        data_quality = "excellent"
    elif n >= 15:
        data_quality = "good"
    elif n >= 5:
        data_quality = "limited"
    else:
        data_quality = "insufficient"

    # ══════════════════════════════════════════════════════════
    # 8. 최근 5분기 트렌드
    # ══════════════════════════════════════════════════════════
    recent = verified[:5] if len(verified) >= 5 else verified
    recent_reactions = [h["reaction_1d_change"] for h in recent]
    recent_avg = sum(recent_reactions) / len(recent_reactions) if recent_reactions else 0
    recent_beat = sum(1 for h in recent if h.get("category") == "Beat")
    if recent_avg > 1.5 and recent_beat >= 3:
        recent_trend = "강세"
    elif recent_avg < -1.5:
        recent_trend = "약세"
    else:
        recent_trend = "보통"

    # ══════════════════════════════════════════════════════════
    # 9. 종합 예측 가능성 점수 (0~100, updated weights)
    # ══════════════════════════════════════════════════════════
    # best factor R² (40%) + direction match (25%) + data qty (15%)
    # + factor consistency (10%) + guidance predictability (10%)
    best_r_sq = max(f["r_squared"] for f in all_factors) if all_factors else 0
    score_r2 = min(best_r_sq / 0.5, 1.0) * 40
    score_dir = (direction_pct / 100) * 25
    score_data = min(n / 40, 1.0) * 15

    # Factor consistency: how many factors agree on direction with meaningful correlation
    factors_with_signal = [f for f in all_factors if f["r_squared"] >= 0.05]
    if len(factors_with_signal) >= 2:
        directions = [f["direction"] for f in factors_with_signal]
        most_common = max(set(directions), key=directions.count)
        consistency = directions.count(most_common) / len(directions)
    else:
        consistency = 0.5
    score_consistency = consistency * 10

    # Guidance predictability: lower guidance_influence = more predictable
    guidance_predict = max(0, 100 - guidance_influence_score) / 100
    score_guidance = guidance_predict * 10

    predictability = round(score_r2 + score_dir + score_data + score_consistency + score_guidance, 1)

    # ══════════════════════════════════════════════════════════
    # 10. Simulation config
    # ══════════════════════════════════════════════════════════
    top_factor_id = top_factors[0]["id"] if top_factors else "eps_surprise"
    top_factor_r_sq = top_factors[0]["r_squared"] if top_factors else 0

    # Find specific factor objects for logic
    eps_f = next((f for f in all_factors if f["id"] == "eps_surprise"), None)
    rev_f = next((f for f in all_factors if f["id"] == "revenue_growth"), None)
    guidance_f = next((f for f in all_factors if f["id"] == "guidance_sentiment"), None)

    if top_factor_id == "eps_surprise" and top_factor_r_sq >= 0.2:
        chart_type = "eps"
        input_factors = ["eps_surprise"]
        if rev_f and rev_f["r_squared"] >= 0.1:
            input_factors.append("revenue_growth")
    elif top_factor_id == "revenue_growth" and top_factor_r_sq >= 0.15:
        chart_type = "revenue"
        input_factors = ["revenue_growth"]
        if eps_f and eps_f["r_squared"] >= 0.1:
            input_factors.append("eps_surprise")
    elif top_factor_id == "guidance_sentiment" and top_factor_r_sq >= 0.15:
        chart_type = "multi"
        input_factors = ["guidance_sentiment"]
        if eps_f and eps_f["r_squared"] >= 0.1:
            input_factors.append("eps_surprise")
    else:
        chart_type = "multi"
        input_factors = [f["id"] for f in top_factors[:3]]

    # guidance_sentiment가 유의미하면 항상 input_factors에 추가
    if guidance_f and guidance_f["r_squared"] >= 0.05 and "guidance_sentiment" not in input_factors:
        input_factors.append("guidance_sentiment")

    simulation_config = {
        "primary_factor": top_factor_id,
        "input_factors": input_factors,
        "chart_type": chart_type,
    }

    # ══════════════════════════════════════════════════════════
    # 11. EPS backward compat fields
    # ══════════════════════════════════════════════════════════
    if eps_r_sq >= 0.3:
        eps_reliability = "high"
    elif eps_r_sq >= 0.1:
        eps_reliability = "medium"
    else:
        eps_reliability = "low"

    if best_r_sq >= 0.3:
        dominant_factor = "EPS" if top_factor_id == "eps_surprise" else "매출"
    elif best_r_sq >= 0.1:
        dominant_factor = "복합"
    else:
        dominant_factor = "기타요인"

    # backward compat guidance_impact
    legacy_guidance_impact = beat_but_down_pct if beat_total >= 3 else None

    # ══════════════════════════════════════════════════════════
    # 12. 프로필 메시지 생성 (dynamic based on top factors)
    # ══════════════════════════════════════════════════════════
    msgs = []

    if top_factors:
        tf = top_factors[0]
        tf_name = tf["name"]
        tf_r_sq = tf["r_squared"]
        tf_id = tf["id"]

        if tf_r_sq >= 0.2:
            msgs.append(
                f"이 종목은 {tf_name}이(가) 주가에 가장 큰 영향을 미칩니다 (R²={tf_r_sq:.2f})."
            )
            # Compare with second factor
            if len(top_factors) >= 2:
                sf = top_factors[1]
                if sf["r_squared"] >= 0.1:
                    msgs.append(f"{sf['name']}도 유의미한 상관관계를 보입니다 (R²={sf['r_squared']:.2f}).")
                elif tf_id != "eps_surprise":
                    msgs.append(f"EPS보다 {tf_name} 추세가 더 중요합니다.")
        elif tf_r_sq >= 0.1:
            msgs.append(
                f"{tf_name}이(가) 주가에 일부 영향을 미칩니다 (R²={tf_r_sq:.2f}). "
                f"매출, 가이던스 등 다른 요인도 함께 고려하세요."
            )
        else:
            msgs.append(
                f"EPS와 매출 모두 주가에 제한적 영향력입니다 (최고 R²={tf_r_sq:.2f}). "
                f"배당, 자사주 매입, 매크로 환경 등이 더 중요할 수 있습니다."
            )

    msgs.append(f"서프라이즈 방향과 주가 방향 일치율: {direction_pct}%")

    if guidance_influence_score >= 30:
        msgs.append(
            f"가이던스 영향이 {guidance_influence_score}%로 실적만으로는 설명이 안 되는 경우도 있습니다."
        )

    if avg_vol >= 5:
        msgs.append(f"실적 발표 시 평균 변동폭 ±{avg_vol}% — 변동성이 큰 종목입니다.")
    elif avg_vol <= 2:
        msgs.append(f"실적 발표 시 평균 변동폭 ±{avg_vol}% — 비교적 안정적입니다.")

    profile_message = " ".join(msgs)

    # ══════════════════════════════════════════════════════════
    # 13. Sector context & factor reasoning (종목별 이유 설명)
    # ══════════════════════════════════════════════════════════
    sector_ctx = SECTOR_CONTEXT.get(sector, {}) if sector else {}
    stock_type_label = sector_ctx.get("type", "기타")
    sector_desc = sector_ctx.get("desc", "")
    sector_notes = sector_ctx.get("notes", {})

    # Classify stock type based on data patterns
    if avg_vol >= 5 and any(f["id"] == "revenue_growth" and f["r_squared"] >= 0.15 for f in all_factors):
        stock_type_label = stock_type_label + " (성장주)"
    elif avg_vol <= 2.5 and eps_r_sq >= 0.15:
        stock_type_label = stock_type_label + " (가치/배당주)"
    elif guidance_influence_score >= 40:
        stock_type_label = stock_type_label + " (가이던스 민감)"

    # Per-factor reasoning
    factor_reasoning = {}
    for f in all_factors:
        fid = f["id"]
        r_sq = f["r_squared"]
        corr = f["correlation"]
        sample = f.get("sample_size", 0)
        weight_pct = round(f["weight"] * 100, 1)

        # Base reasoning from sector context
        base_note = sector_notes.get(fid, "")
        if fid == "guidance_sentiment" and not base_note:
            base_note = "AI가 어닝콜 트랜스크립트를 분석한 감성 점수입니다. CEO/CFO 발언의 긍정/부정 톤과 가이던스 내용을 반영합니다."

        # Data-driven reasoning
        if r_sq >= 0.3:
            strength = "매우 강한 상관관계"
            data_note = f"과거 {sample}건의 데이터에서 이 요인이 주가 변동의 {round(r_sq*100)}%를 설명합니다."
        elif r_sq >= 0.15:
            strength = "유의미한 상관관계"
            data_note = f"과거 {sample}건 중 R²={r_sq:.2f}로, 주가에 의미 있는 영향을 줍니다."
        elif r_sq >= 0.05:
            strength = "약한 상관관계"
            data_note = f"R²={r_sq:.2f}로 단독 설명력은 제한적이나, 다른 요인과 함께 작용합니다."
        else:
            strength = "거의 무관"
            data_note = f"R²={r_sq:.2f}로 이 종목에서는 주가에 큰 영향이 없습니다."

        direction_note = "양의 상관" if corr >= 0 else "음의 상관"

        factor_reasoning[fid] = {
            "strength": strength,
            "sector_note": base_note,
            "data_note": data_note,
            "direction": direction_note,
            "weight_pct": weight_pct,
            "summary": f"{f['name']}: {strength} ({direction_note}, 비중 {weight_pct}%). {base_note} {data_note}".strip(),
        }

    # Generate analysis explanation (왜 이런 분석이 나왔는지)
    explanation_parts = []
    if sector:
        explanation_parts.append(f"이 종목은 {sector} 섹터의 {stock_type_label}입니다.")
    if sector_desc:
        explanation_parts.append(sector_desc)

    explanation_parts.append(f"과거 {n}건의 검증된 실적 데이터를 분석한 결과:")

    for i, tf in enumerate(top_factors[:3]):
        fid = tf["id"]
        r_sq_pct = round(tf["r_squared"] * 100, 1)
        reasoning = factor_reasoning.get(fid, {})
        if i == 0:
            explanation_parts.append(
                f"• 1순위 '{tf['name']}' — 주가 변동의 {r_sq_pct}%를 설명 ({reasoning.get('strength', '')})"
            )
        else:
            explanation_parts.append(
                f"• {i+1}순위 '{tf['name']}' — R²={tf['r_squared']:.2f} ({reasoning.get('strength', '')})"
            )

    if guidance_influence_score >= 30:
        explanation_parts.append(
            f"• 가이던스 영향: EPS를 Beat해도 주가가 하락한 비율이 {beat_but_down_pct}%로, "
            f"실적 외 요인(가이던스, CEO 발언 등)이 주가에 상당한 영향을 미칩니다."
        )

    analysis_explanation = "\n".join(explanation_parts)

    # ══════════════════════════════════════════════════════════
    # 최종 결과 조립
    # ══════════════════════════════════════════════════════════
    result = {
        "factors": all_factors,
        "top_factors": [{"id": f["id"], "name": f["name"], "correlation": f["correlation"],
                         "r_squared": f["r_squared"], "weight": f["weight"]} for f in top_factors],
        "guidance_analysis": guidance_analysis,
        "simulation_config": simulation_config,
        "predictability_score": predictability,
        "avg_volatility": avg_vol,
        "recent_trend": recent_trend,
        "data_quality": data_quality,
        "verified_sample_size": n,
        "profile_message": profile_message,
        # NEW: per-stock reasoning
        "sector": sector or "Unknown",
        "stock_type": stock_type_label,
        "sector_description": sector_desc,
        "factor_reasoning": factor_reasoning,
        "analysis_explanation": analysis_explanation,
        # backward compatibility
        "eps_correlation": eps_corr,
        "eps_r_squared": eps_r_sq,
        "eps_reliability": eps_reliability,
        "dominant_factor": dominant_factor,
        "guidance_impact": legacy_guidance_impact,
        "direction_match_pct": direction_pct,
    }

    return result


# ── 메인 분석 함수 ────────────────────────────────────────

async def get_full_earnings_analysis(ticker: str) -> dict:
    """종목의 전체 어닝 분석 데이터 반환"""
    ticker = ticker.upper()

    # 1. 어닝 서프라이즈 데이터 (캐시 또는 새로 가져오기)
    if not await _is_cache_fresh(ticker, "earnings_surprises"):
        # 기존 캐시 삭제 후 새로 가져오기
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM earnings_surprises WHERE ticker=?", (ticker,))
            await db.execute("DELETE FROM earnings_price_reactions WHERE ticker=?", (ticker,))
            await db.execute(
                "DELETE FROM cache_metadata WHERE ticker=? AND data_type IN ('earnings_surprises','price_reactions')",
                (ticker,))
            await db.commit()
        await _fetch_and_cache_earnings(ticker)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM earnings_surprises WHERE ticker=? ORDER BY period_end DESC",
            (ticker,)
        )
        surprises = [dict(row) for row in await cursor.fetchall()]

    if not surprises:
        return {
            "ticker": ticker,
            "history": [],
            "statistics": {},
            "total_count": 0,
            "beat_rate": 0,
            "next_earnings": None,
        }

    # 2. 주가 반응 계산 — period_end 기준으로 키 관리
    # report_date가 실제 발표일(Yahoo/Finnhub 소스)이면 그걸로 주가 조회
    # SEC 소스의 report_date(filing date)는 부정확하므로 period_end 사용
    # 중복 방지: period_end를 key로, 주가 조회에는 best available date 사용

    # period_end → best date for price lookup 매핑 생성
    date_for_price_map = {}  # {period_end: date_for_price_reaction}
    report_date_counts = {}  # report_date 중복 감지
    for s in surprises:
        rd = s.get("report_date")
        if rd:
            report_date_counts[rd] = report_date_counts.get(rd, 0) + 1

    for s in surprises:
        pe = s.get("period_end")
        if not pe:
            continue
        rd = s.get("report_date")
        # report_date가 여러 분기에 공유되면(SEC 10-K), period_end 사용
        if rd and report_date_counts.get(rd, 0) == 1:
            date_for_price_map[pe] = rd
        else:
            date_for_price_map[pe] = pe

    if not await _is_cache_fresh(ticker, "price_reactions"):
        # 유니크한 날짜만 추출하여 주가 반응 계산
        unique_dates = list(set(date_for_price_map.values()))
        reactions = await asyncio.to_thread(_compute_price_reactions, ticker, unique_dates)
        await _cache_price_reactions(ticker, reactions)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM earnings_price_reactions WHERE ticker=? ORDER BY earnings_date DESC",
            (ticker,)
        )
        reactions_map = {row["earnings_date"]: dict(row) for row in await cursor.fetchall()}

    # 3. 병합 + 분류
    history = []
    for s in surprises:
        pe = s.get("period_end", "")
        price_date = date_for_price_map.get(pe, pe)
        r = reactions_map.get(price_date, {})
        est_verified = bool(s.get("estimate_verified"))
        est_src_count = s.get("estimate_source_count", 0)
        surprise_pct = s.get("surprise_pct")

        # 미검증 estimate → surprise/category를 신뢰할 수 없으므로 별도 표시
        category = _classify(surprise_pct) if est_verified else "Unknown"

        history.append({
            "date": s.get("report_date") or pe,
            "period_end": pe,
            "period": s.get("period"),
            "eps_estimate": s.get("eps_estimate"),
            "eps_actual": s.get("eps_actual"),
            "surprise_pct": surprise_pct if est_verified else None,
            "surprise_pct_raw": surprise_pct,  # 미검증 포함 원본값 (참고용)
            "category": category,
            "estimate_verified": est_verified,
            "estimate_source_count": est_src_count,
            "pre_3d_change": r.get("pre_3d_change"),
            "reaction_1d_change": r.get("reaction_1d_change"),
            "post_3d_change": r.get("post_3d_change"),
            "post_5d_change": r.get("post_5d_change"),
            "close_on_date": r.get("close_on_date"),
        })

    # 4. 통계
    statistics = _compute_statistics(history)

    # 5. 다음 실적 발표일 + 컨센서스 + 매출 데이터 (blocking → thread 병렬)
    def _fetch_overview_and_consensus():
        """동기 호출들을 하나의 스레드에서 실행"""
        _next_earnings = None
        _consensus = None
        _overview = None
        try:
            from app.services.yfinance_client import get_overview
            _overview = get_overview(ticker)
            _next_earnings = _overview.get("earnings_date")
        except Exception:
            pass

        try:
            data = _yf_quoteSummary(ticker, "earnings")
            ec = data.get("earnings", {}).get("earningsChart", {})
            cqe = ec.get("currentQuarterEstimate", {})
            if isinstance(cqe, dict):
                _consensus = cqe.get("raw")
            elif isinstance(cqe, (int, float)):
                _consensus = cqe
        except Exception:
            pass

        if _consensus is None and finnhub_client.is_available():
            try:
                estimates = finnhub_client.get_eps_estimates(ticker)
                est_data = estimates.get("data", [])
                if est_data:
                    _consensus = est_data[0].get("epsAvg")
            except Exception:
                pass

        return _next_earnings, _consensus, _overview

    # 병렬: overview/consensus + 매출추정치 + 매출히스토리
    (next_earnings, current_consensus_eps, overview), revenue_estimates, revenue_history = await asyncio.gather(
        asyncio.to_thread(_fetch_overview_and_consensus),
        asyncio.to_thread(_get_revenue_estimates, ticker),
        asyncio.to_thread(_get_revenue_history, ticker),
    )

    total = len(history)
    verified_total = len([h for h in history if h.get("estimate_verified")])
    # Beat/Miss 비율은 검증된 서프라이즈 데이터만 기준
    classified = [h for h in history if h["category"] in ("Beat", "Meet", "Miss")]
    classified_count = len(classified)
    beat_count = len([h for h in classified if h["category"] == "Beat"])
    miss_count = len([h for h in classified if h["category"] == "Miss"])

    # 8. 종목별 다중 요인 프로필 분석 (가이던스 데이터 포함)
    sector = overview.get("sector") if overview else None

    # 가이던스 캐시에서 sentiment 데이터 가져오기 (DB에 있으면)
    guidance_data_for_profile = []
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT period_end, sentiment_score, key_themes, impact_factor FROM guidance_analysis WHERE ticker=?",
                (ticker,)
            )
            rows = await cursor.fetchall()
            for row in rows:
                d = dict(row)
                guidance_data_for_profile.append(d)
    except Exception:
        pass

    stock_profile = _compute_multi_factor_profile(
        history, revenue_history, sector=sector,
        guidance_data=guidance_data_for_profile if guidance_data_for_profile else None
    )

    return {
        "ticker": ticker,
        "history": history,
        "statistics": statistics,
        "total_count": total,
        "verified_count": verified_total,
        "classified_count": classified_count,
        "beat_rate": round(beat_count / classified_count * 100, 1) if classified_count else 0,
        "miss_rate": round(miss_count / classified_count * 100, 1) if classified_count else 0,
        "next_earnings": next_earnings,
        "current_consensus_eps": current_consensus_eps,
        "revenue_estimates": revenue_estimates,
        "revenue_history": revenue_history,
        "stock_profile": stock_profile,
    }
