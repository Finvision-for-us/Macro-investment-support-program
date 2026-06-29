"""값싼 사전 점수 → Top-K 선별.

목적: LLM 인과 추론·리서치는 비싸다. 그 전에 결정론적·무료 신호만으로 클러스터
중요도를 매겨 상위 K개만 비싼 단계로 넘긴다. 여기 점수는 §8 AI 분석의 정밀 영향도
스코어가 아니라 "버릴 후보를 빠르게 거르는" 사전 필터다. build_story_skeletons의
임시 impact 값으로도 재사용된다.

신호: 보도량(spread) · 신뢰도(trust_tier) · 사건유형 · 직접티커 수 · 최신성.
"""
from __future__ import annotations

from datetime import UTC, datetime

from ..schema import EventCluster

# 사건유형별 시장 영향 가중 (모르면 0.2). m_and_a·실적·AI capex가 상위.
EVENT_TYPE_WEIGHT: dict[str, float] = {
    "m_and_a": 1.0,
    "earnings": 0.8,
    "guidance_up": 0.8,
    "guidance_down": 0.8,
    "ai_capex": 0.8,
    "regulation": 0.7,
    "ipo": 0.7,
    "ceo_change": 0.7,
    "strategic_investment": 0.7,
    "litigation": 0.6,
    "supply_chain": 0.6,
    "cfo_change": 0.5,
    "price_up": 0.5,
    "price_down": 0.5,
    "filing": 0.3,
    "other": 0.2,
}

# 신뢰도 등급 가중 (1=공시 최상).
TIER_WEIGHT: dict[int, float] = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.3}

# 가중 합 계수 (합 = 1.0).
W_SPREAD = 0.30
W_TIER = 0.20
W_EVENT = 0.25
W_TICKER = 0.10
W_RECENCY = 0.15

SPREAD_CAP = 8        # 보도량 포화 지점
TICKER_CAP = 3        # 직접티커 수 포화 지점
RECENCY_HALFLIFE_H = 48.0  # 최신성 선형 감쇠 구간(시간)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _recency_score(published_end: datetime | None, now: datetime) -> float:
    if published_end is None:
        return 0.5  # 타임스탬프 불명 → 중립
    age_h = (now - _aware(published_end)).total_seconds() / 3600.0
    if age_h <= 0:
        return 1.0
    return max(0.0, 1.0 - age_h / RECENCY_HALFLIFE_H)


def prescore(cluster: EventCluster, *, now: datetime | None = None) -> float:
    """클러스터 1개의 사전 점수 [0, 1]."""
    now = now or datetime.now(UTC)

    spread_s = min(cluster.spread, SPREAD_CAP) / SPREAD_CAP
    tier_s = TIER_WEIGHT.get(int(cluster.trust_tier_best), 0.3)
    event_s = max(
        (EVENT_TYPE_WEIGHT.get(e, 0.2) for e in cluster.event_types),
        default=0.2,
    )
    ticker_s = min(len(cluster.tickers_direct), TICKER_CAP) / TICKER_CAP
    recency_s = _recency_score(cluster.published_end, now)

    return (
        W_SPREAD * spread_s
        + W_TIER * tier_s
        + W_EVENT * event_s
        + W_TICKER * ticker_s
        + W_RECENCY * recency_s
    )


def rank(
    clusters: list[EventCluster], *, now: datetime | None = None
) -> list[tuple[EventCluster, float]]:
    """(cluster, score) 내림차순."""
    now = now or datetime.now(UTC)
    scored = [(c, prescore(c, now=now)) for c in clusters]
    scored.sort(key=lambda cs: cs[1], reverse=True)
    return scored


def top_k(
    clusters: list[EventCluster], k: int, *, now: datetime | None = None
) -> list[tuple[EventCluster, float]]:
    """상위 K개만 (cluster, score)로."""
    return rank(clusters, now=now)[:k]
