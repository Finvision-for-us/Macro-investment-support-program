"""§9 최종 랭킹 — AI impact 이후 노출용 Top-N 선택.

§8의 ``aggregated_impact``는 시장 영향도 평가다. §9는 그 위에 편집 규칙을 얹어
광고성 반복, 티커 없는 일반론, 단일 주제 과점이 Top-N을 잠식하지 않게 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.causal.schema import Story

from ..candidates.pipeline import CandidateResult

_WORD_RE = re.compile(r"[a-z0-9가-힣]+")
_LEGAL_SOLICITATION_RE = re.compile(
    r"\b("
    r"class action|lead plaintiff|law firm|shareholder alert|investor alert|"
    r"securities fraud|deadline|losses of|rosen law|levi & korsinsky|"
    r"pomerantz|glancy prongay|bragar eagel"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FinalRankConfig:
    top_n: int = 30
    min_final_score: float = 0.12
    max_per_primary_ticker: int = 2
    max_legal_solicitations: int = 1
    story_bonus: float = 0.08
    deep_bonus: float = 0.04
    no_ticker_penalty: float = 0.12
    legal_solicitation_penalty: float = 0.25
    source_diversity_bonus_cap: float = 0.06


@dataclass(frozen=True)
class RankedStory:
    story: Story
    final_score: float
    reasons: list[str] = field(default_factory=list)


def _story_text(story: Story, result: CandidateResult) -> str:
    parts = [story.title, story.narrative_short, story.narrative_long]
    for eid in story.event_ids:
        ev = result.events_by_id.get(eid)
        if ev:
            parts.extend([ev.title, ev.summary])
    return " ".join(p for p in parts if p)


def _norm_title(story: Story, result: CandidateResult) -> str:
    text = story.title
    if not text and story.event_ids:
        ev = result.events_by_id.get(story.event_ids[0])
        text = ev.title if ev else ""
    return " ".join(_WORD_RE.findall(text.lower()))


def _primary_ticker(story: Story) -> str:
    return story.affected_tickers[0] if story.affected_tickers else "(none)"


def is_legal_solicitation(story: Story, result: CandidateResult) -> bool:
    return bool(_LEGAL_SOLICITATION_RE.search(_story_text(story, result)))


def score_final(
    story: Story,
    result: CandidateResult,
    config: FinalRankConfig | None = None,
) -> RankedStory:
    config = config or FinalRankConfig()
    score = story.aggregated_impact
    reasons: list[str] = [f"impact={story.aggregated_impact:.3f}"]

    if len(story.event_ids) > 1:
        score += config.story_bonus
        reasons.append(f"story_bonus=+{config.story_bonus:.2f}")

    if any(eid in result.deep_reports for eid in story.event_ids):
        score += config.deep_bonus
        reasons.append(f"deep_bonus=+{config.deep_bonus:.2f}")

    source_bonus = min(
        max(0, len(story.all_sources) - 1) * 0.015,
        config.source_diversity_bonus_cap,
    )
    if source_bonus:
        score += source_bonus
        reasons.append(f"source_diversity=+{source_bonus:.2f}")

    if not story.affected_tickers:
        score -= config.no_ticker_penalty
        reasons.append(f"no_ticker=-{config.no_ticker_penalty:.2f}")

    if is_legal_solicitation(story, result):
        score -= config.legal_solicitation_penalty
        reasons.append(f"legal_solicitation=-{config.legal_solicitation_penalty:.2f}")

    return RankedStory(story=story, final_score=max(0.0, score), reasons=reasons)


def rank_final(
    stories: list[Story],
    result: CandidateResult,
    config: FinalRankConfig | None = None,
) -> list[RankedStory]:
    config = config or FinalRankConfig()
    ranked = [score_final(s, result, config) for s in stories]
    ranked.sort(key=lambda item: item.final_score, reverse=True)

    selected: list[RankedStory] = []
    seen_titles: set[str] = set()
    ticker_counts: dict[str, int] = {}
    legal_count = 0

    for item in ranked:
        if item.final_score < config.min_final_score:
            continue

        title_key = _norm_title(item.story, result)
        if title_key and title_key in seen_titles:
            continue

        is_legal = is_legal_solicitation(item.story, result)
        if is_legal and legal_count >= config.max_legal_solicitations:
            continue

        primary = _primary_ticker(item.story)
        if ticker_counts.get(primary, 0) >= config.max_per_primary_ticker:
            continue

        selected.append(item)
        if title_key:
            seen_titles.add(title_key)
        ticker_counts[primary] = ticker_counts.get(primary, 0) + 1
        if is_legal:
            legal_count += 1

        if len(selected) >= config.top_n:
            break

    return selected


def select_final_stories(
    stories: list[Story],
    result: CandidateResult,
    config: FinalRankConfig | None = None,
) -> list[Story]:
    return [item.story for item in rank_final(stories, result, config)]
