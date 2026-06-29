"""M3.5 Day 3: FRED 거시지표 수집 + 1σ 이상 변화 detect (PROJECT_SPEC §11.5).

매 batch 의 ingest 와 병행 실행 — 8개 핵심 시계열을 FRED API 에서 받아
이전 관측 대비 표준편차 ≥ ``sigma_threshold`` 인 변화 지점만
:class:`MacroEvent` 로 추출. 해당 이벤트는 다음 단계에서 종목 뉴스 이벤트와
같은 graph 에 합류 (Day 4).

비용: FRED 무료 (100 req/min). 8 시계열 × 1 fetch = 8 calls, 시계열당 ~180일치.
"""
from __future__ import annotations

import json
import statistics
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from pydantic import BaseModel, Field

from src.config import FRED_API_KEY
from src.cost_guard import log_call

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_LOOKBACK_DAYS = 180  # σ 계산용 히스토리
DEFAULT_EMIT_DAYS = 14  # 이 기간 내 변화만 이벤트로 emit
DEFAULT_SIGMA_THRESHOLD = 1.0


class MissingFredKeyError(RuntimeError):
    """FRED API 키 미설정 — retry 대상에서 제외."""


# 핵심 거시 시계열 메타 — 한국어 라벨로 직접 표시하기 위함
SERIES_META: dict[str, dict[str, str]] = {
    "FEDFUNDS": {"label_ko": "연방기금금리", "unit": "%", "freq": "monthly"},
    "CPIAUCSL": {"label_ko": "CPI (소비자물가지수)", "unit": "index", "freq": "monthly"},
    "DGS10": {"label_ko": "10년물 국채금리", "unit": "%", "freq": "daily"},
    "DCOILWTICO": {"label_ko": "WTI 유가", "unit": "$/배럴", "freq": "daily"},
    "DEXJPUS": {"label_ko": "달러/엔 환율", "unit": "엔/달러", "freq": "daily"},
    "UNRATE": {"label_ko": "실업률", "unit": "%", "freq": "monthly"},
    "T10Y2Y": {"label_ko": "장단기 금리차 (10Y-2Y)", "unit": "%p", "freq": "daily"},
    "VIXCLS": {"label_ko": "VIX (변동성지수)", "unit": "index", "freq": "daily"},
}


class MacroObservation(BaseModel):
    """FRED 1개 관측치 (raw)."""

    date: date
    value: float


class MacroEvent(BaseModel):
    """1σ 이상 변화 — 종목 뉴스 이벤트와 같은 graph 에 합류 가능한 단위."""

    id: str  # macro_FEDFUNDS_20260401
    series_id: str
    series_label_ko: str
    unit: str
    observed_at: datetime  # UTC 자정
    value: float
    prev_value: float
    change: float  # absolute (value - prev_value)
    sigma_z: float  # 표준화된 변화 — emit threshold 통과한 값
    summary_ko: str  # "연방기금금리 4.50% → 4.25% (-0.25%p, -1.8σ)"

    @property
    def is_negative(self) -> bool:
        return self.change < 0


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------


def _check_key() -> str:
    if not FRED_API_KEY:
        raise MissingFredKeyError(
            "FRED_API_KEY not set. Get one at https://fred.stlouisfed.org/docs/api/api_key.html "
            "and add it to .env"
        )
    return FRED_API_KEY


def fetch_series(
    series_id: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> list[MacroObservation]:
    """단일 시계열 fetch — 오름차순 정렬된 관측치 list. 비싸지 않으므로 캐시 안 함."""
    key = api_key or _check_key()
    end = date.today()
    start = end - timedelta(days=lookback_days)
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "sort_order": "asc",
    }
    url = f"{FRED_BASE}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read())
    log_call("fred", f"series:{series_id}", notes=f"{lookback_days}d")
    out: list[MacroObservation] = []
    for o in data.get("observations", []):
        raw = o.get("value", ".")
        if raw == "." or raw == "":  # FRED missing marker
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        out.append(MacroObservation(date=date.fromisoformat(o["date"]), value=v))
    return out


# ---------------------------------------------------------------------------
# Detection (결정론적, 테스트 가능)
# ---------------------------------------------------------------------------


def _changes(obs: list[MacroObservation]) -> list[float]:
    """연속 관측치 차분."""
    return [obs[i].value - obs[i - 1].value for i in range(1, len(obs))]


def _format_value(value: float, unit: str) -> str:
    """라벨/단위 조합 친화적 출력 — 소수점 자리수 조정."""
    if unit == "%" or unit == "%p":
        return f"{value:.2f}{unit}"
    if unit.startswith("$"):
        return f"${value:,.2f}"
    if unit == "엔/달러":
        return f"{value:.2f}엔"
    if unit == "index":
        return f"{value:.2f}"
    return f"{value:.2f}{unit}"


def _make_summary(series_id: str, prev: float, cur: float, z: float) -> str:
    meta = SERIES_META.get(series_id, {"label_ko": series_id, "unit": ""})
    label = meta["label_ko"]
    unit = meta["unit"]
    arrow = "→"
    delta = cur - prev
    sign = "+" if delta >= 0 else ""
    # 변화 표시: %, %p 는 절대 변화, 그 외는 절대값 + 변화율
    if unit in {"%", "%p"}:
        change_str = f"{sign}{delta:.2f}%p"
    else:
        pct = (delta / prev * 100) if prev != 0 else 0.0
        change_str = f"{sign}{delta:.2f} ({sign}{pct:.1f}%)"
    return (
        f"{label} {_format_value(prev, unit)} {arrow} {_format_value(cur, unit)} "
        f"({change_str}, {z:+.1f}σ)"
    )


def detect_events(
    series_id: str,
    observations: list[MacroObservation],
    *,
    sigma_threshold: float = DEFAULT_SIGMA_THRESHOLD,
    emit_after: date | None = None,
) -> list[MacroEvent]:
    """관측치 시계열에서 |Z| ≥ ``sigma_threshold`` 인 변화 지점만 추출.

    Args:
        series_id: e.g. ``"FEDFUNDS"``.
        observations: 오름차순 정렬 가정.
        sigma_threshold: 1.0 이면 평균 변화에서 1표준편차 떨어진 변화만.
        emit_after: 이 날짜 (포함) 이후 관측만 이벤트로 emit. ``None`` 이면 전부.
    """
    if len(observations) < 3:
        return []
    diffs = _changes(observations)
    # 표준편차 — 최소 2개 있어야 계산 가능
    try:
        sigma = statistics.pstdev(diffs)
    except statistics.StatisticsError:
        return []
    if sigma == 0:
        return []

    meta = SERIES_META.get(series_id, {"label_ko": series_id, "unit": ""})

    out: list[MacroEvent] = []
    for i in range(1, len(observations)):
        prev = observations[i - 1]
        cur = observations[i]
        if emit_after is not None and cur.date < emit_after:
            continue
        change = cur.value - prev.value
        z = change / sigma
        if abs(z) < sigma_threshold:
            continue
        ts = datetime.combine(cur.date, datetime.min.time()).replace(tzinfo=timezone.utc)
        out.append(
            MacroEvent(
                id=f"macro_{series_id}_{cur.date.strftime('%Y%m%d')}",
                series_id=series_id,
                series_label_ko=meta["label_ko"],
                unit=meta["unit"],
                observed_at=ts,
                value=cur.value,
                prev_value=prev.value,
                change=change,
                sigma_z=round(z, 3),
                summary_ko=_make_summary(series_id, prev.value, cur.value, z),
            )
        )
    return out


def fetch_macro_events(
    *,
    series_ids: Iterable[str] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    emit_days: int = DEFAULT_EMIT_DAYS,
    sigma_threshold: float = DEFAULT_SIGMA_THRESHOLD,
    fetch_fn: Callable[..., list[MacroObservation]] = fetch_series,
) -> list[MacroEvent]:
    """모든 시리즈 fetch → 최근 ``emit_days`` 일 내 ``sigma_threshold`` σ 변화만.

    ``fetch_fn`` 주입으로 테스트에서 HTTP 없이 검증 가능.
    """
    series_ids = list(series_ids or SERIES_META.keys())
    emit_after = date.today() - timedelta(days=emit_days)
    events: list[MacroEvent] = []
    for sid in series_ids:
        try:
            obs = fetch_fn(sid, lookback_days=lookback_days)
        except MissingFredKeyError:
            raise
        except Exception:  # noqa: BLE001 — 1개 시리즈 실패로 전체 중단 X
            continue
        events.extend(
            detect_events(
                sid,
                obs,
                sigma_threshold=sigma_threshold,
                emit_after=emit_after,
            )
        )
    # 최신 → 과거 순
    events.sort(key=lambda e: e.observed_at, reverse=True)
    return events
