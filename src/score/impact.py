"""영향력 점수 (Impact Score) 계산 — 3 신호 (mcap 제거 후, 2026-05-30~).

공식: Impact = 0.40×Spread* + 0.30×Novelty + 0.30×PriceReaction

* Spread* = effective_spread(보도 개수, publisher diversity, PR wire 디스카운트)
            보도자료 1건의 wire 재게재로 인한 거품 차단.

mcap 신호 제거 이유 (2026-05-30 결정):
- Polygon ticker tagging 이 "본문 언급" 기반이라 단순 비교/배경 언급도 합산됨
- GOOG/GOOGL 같은 클래스 중복 카운트
- "주체 vs 단순 언급" 구분 안 됨 → 보도자료 1건이 시총 $9.4T 처럼 보이는 거품
- 시장이 진짜 반응한 큰 사건은 PriceReaction 이 잡아냄 (시총 가중 평균 내부에서)
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

from src.config import OUTPUTS_DIR
from src.ingest.schema import Event

MCAP_CACHE_PATH = OUTPUTS_DIR / "marketcap_cache.json"
MCAP_CACHE_TTL = timedelta(days=7)

# 3신호 가중치 — 합=1.0
SPREAD_WEIGHT = 0.40
NOVELTY_WEIGHT = 0.30
PRICE_REACTION_WEIGHT = 0.30

NEUTRAL_DEFAULT = 0.5  # 미구현 신호의 중립값

# (B) 단일 publisher 디스카운트 — wire 재게재 의심
SINGLE_PUBLISHER_DISCOUNT = 0.4
TWO_PUBLISHER_DISCOUNT = 0.75

# (C) PR wire 검출 — 보도자료 출처면 더 강하게 디스카운트
_WIRE_PATTERN = re.compile(
    r"(?i)\b("
    r"globenewswire|business\s*wire|prnewswire|pr\s*newswire|"
    r"accesswire|newsfile|access\s*wire|cision|"
    r"sec\s*filing|prweb"
    r")\b"
)
PR_WIRE_ONLY_DISCOUNT = 0.25  # publisher 전부 wire 일 때


@dataclass
class ScoredEvent:
    event: Event
    spread_score: float
    novelty_score: float
    price_reaction_score: float
    impact_score: float
    # 디버그/투명성 — 거품 진단 가능하도록 보존
    effective_spread: float
    spread_discount_factor: float


# ---------- spread 디스카운트 (B + C) ----------------------------------------


def is_pr_wire(publisher: str) -> bool:
    """알려진 보도자료 wire 출처인가."""
    if not publisher:
        return False
    return bool(_WIRE_PATTERN.search(publisher))


def discount_factor(publishers: list[str]) -> float:
    """publisher 다양성과 wire 여부로 spread 디스카운트 계수 계산.

    Returns:
        1.0 = 디스카운트 없음 (정상 — 3+개 매체 + 비-wire 다수)
        0.25~0.75 = 거품 의심
    """
    if not publishers:
        return SINGLE_PUBLISHER_DISCOUNT  # publisher 정보 없으면 최악 가정

    unique = sorted({p for p in publishers if p})
    n_unique = len(unique)
    if n_unique == 0:
        return SINGLE_PUBLISHER_DISCOUNT

    n_wires = sum(1 for p in unique if is_pr_wire(p))
    all_wires = n_wires == n_unique

    # 모든 publisher 가 wire → 강한 디스카운트
    if all_wires:
        return PR_WIRE_ONLY_DISCOUNT

    # 단일 publisher
    if n_unique == 1:
        return SINGLE_PUBLISHER_DISCOUNT

    # 2개 publisher — 둘 다 wire 아니면 0.75, 하나가 wire 면 0.6
    if n_unique == 2:
        return 0.6 if n_wires > 0 else TWO_PUBLISHER_DISCOUNT

    # 3+ publisher — 정상. wire 비중 있으면 살짝 깎음
    if n_wires > 0:
        # 5개 중 2개가 wire → factor = 1 - 0.4*(2/5) = 0.84
        return max(0.6, 1.0 - 0.4 * (n_wires / n_unique))

    return 1.0


def effective_spread(spread: int, publishers: list[str]) -> tuple[float, float]:
    """디스카운트 적용된 effective spread 와 그 factor.

    Returns:
        (effective_spread, factor) — factor 는 ScoredEvent 에 보존돼 디버깅에 사용.
    """
    if spread <= 0:
        return 0.0, 1.0
    factor = discount_factor(publishers)
    return max(spread * factor, 1.0), factor  # 최소 1 보존 (log 안전)


# ---------- 점수 함수 ---------------------------------------------------------


def spread_score(spread: float, max_spread: float) -> float:
    """log 정규화. spread<=1 → 0, spread=max → 1."""
    if max_spread <= 1:
        return 0.0
    return math.log(spread) / math.log(max_spread) if spread > 1 else 0.0


# ---------- yfinance 시총 (PriceReaction 내부 가중치용으로만 유지) ----------


def _load_cache() -> dict[str, dict]:
    if not MCAP_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(MCAP_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    MCAP_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _is_fresh(entry: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        return datetime.now(timezone.utc) - fetched < MCAP_CACHE_TTL
    except (KeyError, ValueError):
        return False


def fetch_market_caps(tickers: list[str]) -> dict[str, float]:
    """티커 → 시가총액(USD). PriceReaction 내부 시총 가중 평균용. 캐시 7일.

    mcap 신호 자체는 제거됐지만 (B+C 결정) PriceReaction 의 다종목 가중에는 여전히 유용.
    """
    cache = _load_cache()
    result: dict[str, float] = {}
    to_fetch = []

    for t in tickers:
        entry = cache.get(t)
        if entry and _is_fresh(entry):
            result[t] = float(entry["market_cap"] or 0)
        else:
            to_fetch.append(t)

    for t in to_fetch:
        cap = _fetch_one(t)
        result[t] = cap
        cache[t] = {
            "market_cap": cap,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    if to_fetch:
        _save_cache(cache)
    return result


def _fetch_one(ticker: str) -> float:
    try:
        info = yf.Ticker(ticker).info
        cap = info.get("marketCap")
        return float(cap) if cap else 0.0
    except Exception:
        return 0.0


# ---------- 메인 ranker ------------------------------------------------------


def score_events(
    events: list[Event],
    novelty_scores: dict[str, float] | None = None,
    price_reaction_scores: dict[str, float] | None = None,
) -> list[ScoredEvent]:
    """이벤트 리스트에 3신호 합산 영향력 점수를 매겨 내림차순 정렬.

    누락된 신호는 NEUTRAL_DEFAULT(=0.5) 사용. mcap 신호는 더 이상 사용하지 않음.
    """
    if not events:
        return []

    novelty_scores = novelty_scores or {}
    price_reaction_scores = price_reaction_scores or {}

    # effective spread 계산 (publisher diversity + PR wire 디스카운트)
    eff_spreads_factors = [effective_spread(e.spread, e.publishers) for e in events]
    eff_spreads = [es for es, _ in eff_spreads_factors]
    max_eff = max(eff_spreads) if eff_spreads else 1.0

    scored: list[ScoredEvent] = []
    for ev, (eff, factor) in zip(events, eff_spreads_factors, strict=False):
        sp = spread_score(eff, max_eff)
        nov = novelty_scores.get(ev.id, NEUTRAL_DEFAULT)
        pr = price_reaction_scores.get(ev.id, NEUTRAL_DEFAULT)
        impact = (
            SPREAD_WEIGHT * sp
            + NOVELTY_WEIGHT * nov
            + PRICE_REACTION_WEIGHT * pr
        )
        scored.append(
            ScoredEvent(
                event=ev,
                spread_score=sp,
                novelty_score=nov,
                price_reaction_score=pr,
                impact_score=impact,
                effective_spread=eff,
                spread_discount_factor=factor,
            )
        )

    scored.sort(key=lambda s: -s.impact_score)
    return scored


def serialize_scored(scored: list[ScoredEvent]) -> list[dict]:
    return [
        {
            "rank": i + 1,
            "impact_score": round(s.impact_score, 4),
            "spread_score": round(s.spread_score, 4),
            "novelty_score": round(s.novelty_score, 4),
            "price_reaction_score": round(s.price_reaction_score, 4),
            "effective_spread": round(s.effective_spread, 4),
            "spread_discount_factor": round(s.spread_discount_factor, 4),
            "event": s.event.model_dump(mode="json"),
        }
        for i, s in enumerate(scored)
    ]


def load_events(path: Path) -> list[Event]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Event(**d) for d in data]
