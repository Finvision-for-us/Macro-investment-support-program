"""
8단계 경기 사이클 포지셔닝 시스템 (고도화 v2)

모든 지표를 롤링 Z-score로 정규화하여 시대별 절대값 차이를 제거.
"그 시대 기준으로 높은지/낮은지"를 비교하여 패턴 범위를 좁힘.

고도화:
  1. 롤링 Z-score 정규화 (시대별 절대값 차이 제거)
  2. Z-score 기반 연속 점수 (정규분포 확률)
  3. 방향성(트렌드) 매칭
  4. 선행/동행/후행 지표 분리
  5. Phase 순서 제약
"""

import math
import time
from datetime import datetime, date
from statistics import median, stdev, mean
from app.services.fred_client import fetch_series, get_latest_value

# ---------------------------------------------------------------------------
# NBER 공식 경기순환 날짜
# ---------------------------------------------------------------------------
NBER_CYCLES = [
    {"peak": "1960-04-01", "trough": "1961-02-01"},
    {"peak": "1969-12-01", "trough": "1970-11-01"},
    {"peak": "1973-11-01", "trough": "1975-03-01"},
    {"peak": "1980-01-01", "trough": "1980-07-01"},
    {"peak": "1981-07-01", "trough": "1982-11-01"},
    {"peak": "1990-07-01", "trough": "1991-03-01"},
    {"peak": "2001-03-01", "trough": "2001-11-01"},
    {"peak": "2007-12-01", "trough": "2009-06-01"},
    {"peak": "2020-02-01", "trough": "2020-04-01"},
]

PHASE_NAMES = {
    1: "바닥", 2: "초반 강세", 3: "중반 강세", 4: "후반 강세",
    5: "꼭대기", 6: "초반 약세", 7: "중반 약세", 8: "후반 약세",
}
PHASE_NAMES_EN = {
    1: "Bottom", 2: "Early Expansion", 3: "Mid Expansion", 4: "Late Expansion",
    5: "Peak", 6: "Early Contraction", 7: "Mid Contraction", 8: "Late Contraction",
}

PHASE_SECTORS = {
    1: {"recommended": ["임의소비재 (XLY)", "금융 (XLF)"], "caution": ["유틸리티 (XLU)"]},
    2: {"recommended": ["기술 (XLK)", "산업재 (XLI)"], "caution": ["유틸리티 (XLU)", "에너지 (XLE)"]},
    3: {"recommended": ["기술 (XLK)", "산업재 (XLI)"], "caution": ["유틸리티 (XLU)"]},
    4: {"recommended": ["에너지 (XLE)", "원자재 (XLB)"], "caution": ["기술 (XLK)"]},
    5: {"recommended": ["에너지 (XLE)", "헬스케어 (XLV)"], "caution": ["금융 (XLF)", "기술 (XLK)"]},
    6: {"recommended": ["헬스케어 (XLV)", "유틸리티 (XLU)", "필수소비재 (XLP)"], "caution": ["금융 (XLF)"]},
    7: {"recommended": ["유틸리티 (XLU)", "필수소비재 (XLP)", "헬스케어 (XLV)"], "caution": ["산업재 (XLI)", "임의소비재 (XLY)"]},
    8: {"recommended": ["유틸리티 (XLU)", "필수소비재 (XLP)"], "caution": ["임의소비재 (XLY)", "산업재 (XLI)"]},
}

# ---------------------------------------------------------------------------
# 지표 설정
# ---------------------------------------------------------------------------
INDICATOR_CONFIG = {
    "GDP":      {"freq": "quarterly"},
    "UNRATE":   {"freq": "monthly"},
    "CPIAUCSL": {"freq": "monthly"},
    "DFF":      {"freq": "daily"},
    "PCE":      {"freq": "monthly"},
    "UMCSENT":  {"freq": "monthly"},
    "INDPRO":   {"freq": "monthly"},
    "T10YIE":   {"freq": "daily"},
    "T10Y2Y":   {"freq": "daily"},
    "ICSA":     {"freq": "weekly"},
    "HOUST":    {"freq": "monthly"},
    "TCU":      {"freq": "monthly"},
}

# 롤링 Z-score 윈도우 (데이터포인트 수)
ROLLING_WINDOW = {
    "quarterly": 40,   # ~10년
    "monthly": 120,    # 10년
    "weekly": 520,     # 10년
    "daily": 2520,     # ~10년
}

# ---------------------------------------------------------------------------
# 방향성 매칭: Phase별 기대 방향
# ---------------------------------------------------------------------------
EXPECTED_DIRECTION = {
    1: {"GDP": "up",    "UNRATE": "down", "CPIAUCSL": "down", "DFF": "down",
        "PCE": "up",    "UMCSENT": "up",  "INDPRO": "up",    "T10YIE": "flat",
        "T10Y2Y": "up", "ICSA": "down",   "HOUST": "up",     "TCU": "up"},
    2: {"GDP": "up",    "UNRATE": "down", "CPIAUCSL": "flat", "DFF": "up",
        "PCE": "up",    "UMCSENT": "up",  "INDPRO": "up",    "T10YIE": "flat",
        "T10Y2Y": "up", "ICSA": "down",   "HOUST": "up",     "TCU": "up"},
    3: {"GDP": "flat",  "UNRATE": "flat", "CPIAUCSL": "up",   "DFF": "up",
        "PCE": "flat",  "UMCSENT": "flat","INDPRO": "flat",   "T10YIE": "flat",
        "T10Y2Y": "flat","ICSA": "flat",  "HOUST": "flat",    "TCU": "flat"},
    4: {"GDP": "down",  "UNRATE": "flat", "CPIAUCSL": "up",   "DFF": "up",
        "PCE": "down",  "UMCSENT": "down","INDPRO": "down",   "T10YIE": "up",
        "T10Y2Y": "down","ICSA": "up",   "HOUST": "down",    "TCU": "down"},
    5: {"GDP": "down",  "UNRATE": "up",   "CPIAUCSL": "up",   "DFF": "up",
        "PCE": "down",  "UMCSENT": "down","INDPRO": "down",   "T10YIE": "up",
        "T10Y2Y": "down","ICSA": "up",   "HOUST": "down",    "TCU": "down"},
    6: {"GDP": "down",  "UNRATE": "up",   "CPIAUCSL": "flat", "DFF": "down",
        "PCE": "down",  "UMCSENT": "down","INDPRO": "down",   "T10YIE": "down",
        "T10Y2Y": "up", "ICSA": "up",    "HOUST": "down",    "TCU": "down"},
    7: {"GDP": "down",  "UNRATE": "up",   "CPIAUCSL": "down", "DFF": "down",
        "PCE": "down",  "UMCSENT": "down","INDPRO": "down",   "T10YIE": "down",
        "T10Y2Y": "up", "ICSA": "up",    "HOUST": "down",    "TCU": "down"},
    8: {"GDP": "up",    "UNRATE": "up",   "CPIAUCSL": "down", "DFF": "down",
        "PCE": "up",    "UMCSENT": "up",  "INDPRO": "up",     "T10YIE": "down",
        "T10Y2Y": "up", "ICSA": "down",  "HOUST": "up",      "TCU": "up"},
}

# ---------------------------------------------------------------------------
# 선행/동행/후행 지표 분류
# ---------------------------------------------------------------------------
INDICATOR_TYPE = {
    "T10Y2Y": "leading", "ICSA": "leading", "HOUST": "leading", "UMCSENT": "leading",
    "UNRATE": "coincident", "INDPRO": "coincident", "TCU": "coincident",
    "GDP": "coincident", "PCE": "coincident",
    "CPIAUCSL": "lagging", "DFF": "lagging", "T10YIE": "lagging",
}
PHASE_SHIFT = {"leading": 1, "coincident": 0, "lagging": -1}

# Phase 순서 제약
ADJACENCY_BONUS = 0.15
OUTLIER_FACTOR = 2.0
MIN_CYCLES_FOR_PATTERN = 5

# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------
_cache = {"patterns": None, "raw_data": None, "zscore_data": None,
          "last_phase": None, "timestamp": 0}
CACHE_TTL = 24 * 60 * 60


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _build_spans():
    spans = []
    for i, cycle in enumerate(NBER_CYCLES):
        peak = _parse_date(cycle["peak"])
        trough = _parse_date(cycle["trough"])
        spans.append({"type": "contraction", "start": peak, "end": trough, "cycle_index": i})
        if i + 1 < len(NBER_CYCLES):
            next_peak = _parse_date(NBER_CYCLES[i + 1]["peak"])
            spans.append({"type": "expansion", "start": trough, "end": next_peak, "cycle_index": i})
    return spans


def classify_date_to_phase(d: date, spans: list) -> int | None:
    for span in spans:
        if d < span["start"] or d > span["end"]:
            continue
        total_days = (span["end"] - span["start"]).days
        if total_days <= 0:
            continue
        pct = (d - span["start"]).days / total_days
        if span["type"] == "expansion":
            if pct <= 0.10:   return 1
            elif pct <= 0.40: return 2
            elif pct <= 0.70: return 3
            elif pct <= 0.95: return 4
            else:             return 5
        else:
            if pct <= 0.33:   return 6
            elif pct <= 0.66: return 7
            else:             return 8
    return None


# ---------------------------------------------------------------------------
# 롤링 Z-score 정규화
# ---------------------------------------------------------------------------
def _compute_rolling_zscore(series: list[dict], window: int) -> list[dict]:
    """
    각 시점에서 최근 window개 데이터의 평균/표준편차 대비
    현재 값의 Z-score를 계산한다.
    "그 시대 기준으로 높은지/낮은지"를 나타낸다.
    """
    result = []
    values_only = []

    for point in series:
        if point["value"] is None:
            continue
        values_only.append(point["value"])

        if len(values_only) < max(window // 4, 20):
            # 최소 데이터가 부족하면 스킵
            continue

        # 롤링 윈도우
        win = values_only[-window:] if len(values_only) >= window else values_only[:]
        m = mean(win)
        s = stdev(win) if len(win) > 1 else 0.01
        if s < 0.001:
            s = 0.001

        z = (point["value"] - m) / s
        result.append({"date": point["date"], "value": round(z, 4), "raw_value": point["value"]})

    return result


def _fetch_all_indicator_data() -> tuple[dict, dict]:
    """
    12개 지표의 과거 데이터를 가져와서 롤링 Z-score로 변환한다.
    반환: (zscore_data, raw_data)
    """
    zscore_data = {}
    raw_data = {}

    # 일간/주간은 과거 사이클 커버를 위해 더 많은 데이터 필요
    FETCH_LIMIT = {
        "quarterly": 3000,
        "monthly": 3000,
        "weekly": 5000,
        "daily": 20000,
    }

    for series_id, config in INDICATOR_CONFIG.items():
        limit = FETCH_LIMIT.get(config["freq"], 3000)
        raw = fetch_series(series_id, limit=limit)
        if not raw:
            zscore_data[series_id] = []
            raw_data[series_id] = []
            continue

        # None 제거
        clean = [d for d in raw if d["value"] is not None]
        raw_data[series_id] = clean

        # 롤링 Z-score 변환
        window = ROLLING_WINDOW.get(config["freq"], 120)
        zscore_data[series_id] = _compute_rolling_zscore(clean, window)

    return zscore_data, raw_data


# ---------------------------------------------------------------------------
# 일간/주간 → 월간 리샘플링 (패턴 추출용)
# ---------------------------------------------------------------------------
def _resample_to_monthly(series: list[dict]) -> list[dict]:
    """일간/주간 데이터를 월간 평균으로 리샘플링."""
    from collections import defaultdict
    monthly: dict[str, list[float]] = defaultdict(list)
    for point in series:
        key = point["date"][:7]  # "YYYY-MM"
        monthly[key].append(point["value"])
    result = []
    for ym in sorted(monthly.keys()):
        vals = monthly[ym]
        result.append({"date": f"{ym}-15", "value": round(mean(vals), 4)})
    return result


# ---------------------------------------------------------------------------
# 패턴 추출 (Z-score 기반)
# ---------------------------------------------------------------------------
def _extract_patterns(zscore_data: dict) -> dict:
    """
    각 Phase × 지표에 대해 Z-score 기반 패턴을 추출.
    패턴 범위가 시대에 무관하게 좁아짐.
    일간/주간 데이터는 월간으로 리샘플링하여 월간 지표와 동일한 단위로 비교.
    """
    spans = _build_spans()
    patterns = {p: {} for p in range(1, 9)}

    for series_id, series_data in zscore_data.items():
        if not series_data:
            for p in range(1, 9):
                patterns[p][series_id] = None
            continue

        # 일간/주간 지표는 월간으로 리샘플링
        freq = INDICATOR_CONFIG[series_id]["freq"]
        if freq in ("daily", "weekly"):
            series_data = _resample_to_monthly(series_data)

        phase_cycle_values: dict[int, dict[int, list[float]]] = {p: {} for p in range(1, 9)}

        for point in series_data:
            d = _parse_date(point["date"])
            phase = classify_date_to_phase(d, spans)
            if phase is None:
                continue
            for span in spans:
                if span["start"] <= d <= span["end"]:
                    cycle_idx = span["cycle_index"]
                    break
            else:
                continue
            if cycle_idx not in phase_cycle_values[phase]:
                phase_cycle_values[phase][cycle_idx] = []
            phase_cycle_values[phase][cycle_idx].append(point["value"])

        for phase in range(1, 9):
            cycle_data = phase_cycle_values[phase]
            if len(cycle_data) < MIN_CYCLES_FOR_PATTERN:
                patterns[phase][series_id] = None
                continue

            cycle_medians = [median(v) for v in cycle_data.values() if v]
            if len(cycle_medians) < MIN_CYCLES_FOR_PATTERN:
                patterns[phase][series_id] = None
                continue

            # IQR 이상치 검출
            sorted_m = sorted(cycle_medians)
            n = len(sorted_m)
            q1 = sorted_m[n // 4]
            q3 = sorted_m[(3 * n) // 4]
            iqr = q3 - q1
            lb = q1 - OUTLIER_FACTOR * iqr
            ub = q3 + OUTLIER_FACTOR * iqr

            if any(v < lb or v > ub for v in cycle_medians):
                patterns[phase][series_id] = None
            else:
                m = mean(cycle_medians)
                s = stdev(cycle_medians) if len(cycle_medians) > 1 else 0.01
                patterns[phase][series_id] = {
                    "mean": round(m, 3),
                    "std": round(s, 3),
                    "min": round(min(cycle_medians), 3),
                    "max": round(max(cycle_medians), 3),
                }

    return patterns


# ---------------------------------------------------------------------------
# 캐시 관리
# ---------------------------------------------------------------------------
def _get_patterns():
    now = time.time()
    if _cache["patterns"] is not None and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["patterns"], _cache["zscore_data"], _cache["raw_data"]

    zscore_data, raw_data = _fetch_all_indicator_data()
    patterns = _extract_patterns(zscore_data)

    _cache["patterns"] = patterns
    _cache["zscore_data"] = zscore_data
    _cache["raw_data"] = raw_data
    _cache["timestamp"] = now
    return patterns, zscore_data, raw_data


# ---------------------------------------------------------------------------
# 현재 지표값 수집 (Z-score + 원본)
# ---------------------------------------------------------------------------
def _get_current_values(zscore_data: dict, raw_data: dict) -> tuple[dict, dict]:
    """현재 Z-score 값과 원본 값을 반환."""
    current_z = {}
    current_raw = {}

    for series_id in INDICATOR_CONFIG:
        # Z-score 최신값
        zdata = zscore_data.get(series_id, [])
        if zdata:
            current_z[series_id] = zdata[-1]["value"]
            current_raw[series_id] = zdata[-1].get("raw_value")
        else:
            current_z[series_id] = None
            current_raw[series_id] = None

    return current_z, current_raw


# ---------------------------------------------------------------------------
# 방향성(트렌드) 계산
# ---------------------------------------------------------------------------
def _compute_current_trends(raw_data: dict) -> dict[str, str]:
    """각 지표의 최근 트렌드를 원본 데이터 기반으로 계산."""
    trends = {}
    for series_id, config in INDICATOR_CONFIG.items():
        data = raw_data.get(series_id, [])
        if len(data) < 4:
            trends[series_id] = "flat"
            continue

        # 최근 1/3 vs 이전 1/3 비교
        n = min(len(data), 24 if config["freq"] == "monthly" else
                           8 if config["freq"] == "quarterly" else 180)
        recent = data[-n:]
        mid = len(recent) // 2
        recent_avg = mean(d["value"] for d in recent[mid:])
        older_avg = mean(d["value"] for d in recent[:mid])

        if older_avg != 0:
            diff_pct = (recent_avg - older_avg) / abs(older_avg) * 100
        else:
            diff_pct = recent_avg - older_avg

        if diff_pct > 5:
            trends[series_id] = "up"
        elif diff_pct < -5:
            trends[series_id] = "down"
        else:
            trends[series_id] = "flat"

    return trends


def _direction_score(actual: str, expected: str) -> float:
    if expected == "flat":
        return 0.0
    if actual == expected:
        return 1.0
    if actual == "flat":
        return 0.3
    return -0.3


def _zscore_to_score(value: float, pat_mean: float, pat_std: float) -> float:
    if pat_std <= 0:
        pat_std = 0.01
    z = abs(value - pat_mean) / pat_std
    return math.exp(-0.5 * z * z)


def _phase_distance(a: int, b: int) -> int:
    diff = abs(a - b)
    return min(diff, 8 - diff)


# ---------------------------------------------------------------------------
# 현재 Phase 판단
# ---------------------------------------------------------------------------
def determine_current_phase() -> dict:
    patterns, zscore_data, raw_data = _get_patterns()
    current_z, current_raw = _get_current_values(zscore_data, raw_data)
    current_trends = _compute_current_trends(raw_data)
    last_phase = _cache.get("last_phase")

    phase_scores = []
    phase_details = {}

    for phase in range(1, 9):
        phase_patterns = patterns[phase]
        total_score = 0.0
        max_possible_score = 0.0
        indicator_results = {}

        for series_id, pattern in phase_patterns.items():
            cur_z = current_z.get(series_id)
            cur_raw = current_raw.get(series_id)
            cur_trend = current_trends.get(series_id, "flat")
            ind_type = INDICATOR_TYPE.get(series_id, "coincident")
            expected_dir = EXPECTED_DIRECTION.get(phase, {}).get(series_id, "flat")

            if pattern is None:
                indicator_results[series_id] = {
                    "value": round(cur_raw, 2) if cur_raw is not None else None,
                    "z_value": round(cur_z, 3) if cur_z is not None else None,
                    "pattern_range": None,
                    "match": False,
                    "no_pattern": True,
                    "z_score": None,
                    "trend": cur_trend,
                    "expected_trend": expected_dir,
                    "trend_match": None,
                    "indicator_type": ind_type,
                }
                continue

            # Z-score 점수
            if cur_z is not None:
                z_sc = _zscore_to_score(cur_z, pattern["mean"], pattern["std"])
                in_range = pattern["min"] <= cur_z <= pattern["max"]
            else:
                z_sc = 0.0
                in_range = False

            # 방향성 점수
            dir_sc = _direction_score(cur_trend, expected_dir)

            # 선행/후행 가중치
            type_weight = 0.8 if PHASE_SHIFT.get(ind_type, 0) != 0 else 1.0

            # 종합 점수
            combined = (z_sc * 0.7 + max(dir_sc, 0) * 0.3) * type_weight
            if dir_sc < 0:
                combined *= (1 + dir_sc)

            total_score += combined
            max_possible_score += 1.0 * type_weight

            indicator_results[series_id] = {
                "value": round(cur_raw, 2) if cur_raw is not None else None,
                "z_value": round(cur_z, 3) if cur_z is not None else None,
                "pattern_range": [pattern["min"], pattern["max"]],
                "pattern_mean": pattern["mean"],
                "z_score": round(z_sc, 3) if cur_z is not None else None,
                "match": in_range,
                "no_pattern": False,
                "trend": cur_trend,
                "expected_trend": expected_dir,
                "trend_match": (cur_trend == expected_dir) if expected_dir != "flat" else None,
                "indicator_type": ind_type,
            }

        # Phase 순서 제약
        if last_phase is not None:
            dist = _phase_distance(phase, last_phase)
            if dist <= 1:
                total_score *= (1 + ADJACENCY_BONUS)
            elif dist >= 3:
                total_score *= (1 - ADJACENCY_BONUS * 0.5)

        normalized_score = total_score / max_possible_score if max_possible_score > 0 else 0

        phase_scores.append({
            "phase": phase,
            "name": PHASE_NAMES[phase],
            "score": round(normalized_score, 3),
            "raw_score": round(total_score, 3),
            "total_patterns": sum(1 for p in phase_patterns.values() if p is not None),
        })
        phase_details[phase] = indicator_results

    # 최고 점수 Phase
    phase_scores.sort(key=lambda x: x["score"], reverse=True)
    best_phase = phase_scores[0]["phase"]

    # confidence
    best_score = phase_scores[0]["score"]
    second_score = phase_scores[1]["score"] if len(phase_scores) > 1 else 0
    gap_factor = 1.0
    if second_score > 0:
        gap_factor = min(1.0, 0.5 + (best_score - second_score) / second_score)
    confidence = round(best_score * gap_factor, 2)

    _cache["last_phase"] = best_phase

    sectors = PHASE_SECTORS.get(best_phase, {"recommended": [], "caution": []})

    # metrics: 원본 값을 반환 (사용자가 읽기 쉽게)
    metrics = {sid: (round(v, 2) if v is not None else None) for sid, v in current_raw.items()}

    return {
        "phase": best_phase,
        "phase_name": PHASE_NAMES[best_phase],
        "phase_name_en": PHASE_NAMES_EN[best_phase],
        "confidence": confidence,
        "matching_indicators": phase_details[best_phase],
        "recommended_sectors": sectors["recommended"],
        "caution_sectors": sectors["caution"],
        "metrics": metrics,
        "all_phases": sorted(phase_scores, key=lambda x: x["phase"]),
    }
