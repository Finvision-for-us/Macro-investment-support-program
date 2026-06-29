"""1차 필터 — "누가 봐도 아닌 것"만 약하게 제거. 신뢰도 등급 인지.

설계 원칙(DESIGN §4): 좋은 뉴스를 완벽히 고르는 게 아니라 명백 탈락만 친다.
tier-1(SEC 공시)은 사실 기준점이라 카테고리/광고로 절대 탈락시키지 않는다 — recency만 본다.
tier 2~3(RSS·뉴스API)은 카테고리/광고도 본다. 내용이 아예 없는 것(제목·요약 모두 빈)은
모든 tier에서 탈락(empty)하되, 짧은 헤드라인은 살린다(짧음≠쓸모없음).

D9: 컷오프 24h, 발행시간 없음=통과+플래그(no_timestamp), 미국시장 관련성 필터는 생략.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..schema import FilterStatus, NewsItem

DEFAULT_CUTOFF_HOURS = 24

OFF_TOPIC = (
    "sports", "entertainment", "lifestyle", "travel", "recipe",
    "celebrity", "horoscope", "obituary", "fashion",
)
_OFF_RE = re.compile(r"\b(" + "|".join(OFF_TOPIC) + r")\b", re.IGNORECASE)

SPAM_KEYWORDS = (
    "buy now", "guaranteed", "100% return", "hot stock",
    "free trial", "get rich", "강력 추천", "무조건 상승", "리딩방",
)


@dataclass
class FilterResult:
    status: FilterStatus
    reasons: list[str] = field(default_factory=list)  # 탈락 사유(rejected일 때)
    flags: list[str] = field(default_factory=list)     # 통과했지만 주의표시


def classify(
    item: NewsItem,
    now: datetime | None = None,
    cutoff_hours: int = DEFAULT_CUTOFF_HOURS,
) -> FilterResult:
    now = now or datetime.now(UTC)
    reasons: list[str] = []
    flags: list[str] = []

    # recency — 모든 tier. 발행시간 없으면 통과+플래그(D9).
    if item.published_at is not None:
        age_h = (now - item.published_at).total_seconds() / 3600
        if age_h > cutoff_hours:
            reasons.append("too_old")
    else:
        flags.append("no_timestamp")

    # 내용이 아예 없음 — 모든 tier (분석 불가). 짧은 헤드라인은 살림.
    if not item.title.strip() and not item.summary.strip():
        reasons.append("empty")

    # 카테고리/광고 — tier 2~3만 (품질 휴리스틱, tier-1 SEC 면제).
    if item.trust_tier >= 2:
        cat_tokens = {c.strip().lower() for c in (item.raw_category or "").split(",")}
        if cat_tokens & set(OFF_TOPIC) or _OFF_RE.search(item.title):
            reasons.append("off_topic_category")

        text = f"{item.title} {item.summary}".lower()
        if any(kw in text for kw in SPAM_KEYWORDS):
            reasons.append("spam_like")

    status: FilterStatus = "rejected" if reasons else "passed"
    return FilterResult(status, reasons, flags)


def run_filter(news_store, cutoff_hours: int = DEFAULT_CUTOFF_HOURS, now: datetime | None = None):
    """저장된 pending 항목을 분류해 store에 반영. 분포 통계 반환."""
    now = now or datetime.now(UTC)
    stats = {"passed": 0, "rejected": 0, "reasons": Counter(), "flags": Counter()}
    for item in list(news_store.iter_items()):
        if item.filter_status != "pending":
            continue
        res = classify(item, now=now, cutoff_hours=cutoff_hours)
        news_store.set_filter(item.item_id, res.status, res.reasons, res.flags)
        stats[res.status] += 1
        for r in res.reasons:
            stats["reasons"][r] += 1
        for f in res.flags:
            stats["flags"][f] += 1
    return stats
