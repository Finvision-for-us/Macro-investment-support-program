"""§9 최종 랭킹 — 영향도 이후 편집 규칙."""
from __future__ import annotations

from datetime import UTC, datetime

from ingest2.candidates.pipeline import CandidateResult
from ingest2.rank.final import FinalRankConfig, is_legal_solicitation, rank_final
from src.causal.schema import Story
from src.ingest.schema import Event


def mk_event(eid: str, title: str = "", tickers=("AAA",)) -> Event:
    return Event(
        id=eid,
        title=title or f"{eid} headline",
        summary=f"{eid} summary",
        occurred_at=datetime(2026, 6, 29, tzinfo=UTC),
        source_urls=[f"https://example.com/{eid}"],
        publishers=["test"],
        tickers_mentioned=list(tickers),
        spread=1,
    )


def mk_story(
    sid: str,
    event_ids: list[str],
    *,
    impact: float,
    tickers=("AAA",),
    title: str = "",
    sources=(),
) -> Story:
    return Story(
        id=sid,
        event_ids=event_ids,
        title=title or f"{sid} title",
        affected_tickers=list(tickers),
        aggregated_impact=impact,
        all_sources=list(sources),
    )


def mk_result(stories: list[Story], events: list[Event], deep=()) -> CandidateResult:
    return CandidateResult(
        stories=stories,
        events_by_id={e.id: e for e in events},
        edges=[],
        shallow_reports={},
        deep_reports={eid: {"event_id": eid} for eid in deep},
        prescore_by_id={},
        stats={},
    )


def test_default_final_rank_keeps_up_to_30_items():
    assert FinalRankConfig().top_n == 30


def test_story_and_deep_bonuses_can_lift_chain():
    story = mk_story("story", ["e1", "e2"], impact=0.55, sources=["a", "b"])
    signal = mk_story("signal", ["e3"], impact=0.60)
    result = mk_result(
        [story, signal],
        [mk_event("e1"), mk_event("e2"), mk_event("e3")],
        deep=["e1"],
    )
    ranked = rank_final([story, signal], result)
    assert ranked[0].story.id == "story"
    assert ranked[0].final_score > signal.aggregated_impact


def test_no_ticker_penalty_demotes_generic_item():
    generic = mk_story("generic", ["e1"], impact=0.50, tickers=())
    specific = mk_story("specific", ["e2"], impact=0.45, tickers=("NVDA",))
    result = mk_result([generic, specific], [mk_event("e1"), mk_event("e2")])
    ranked = rank_final([generic, specific], result)
    assert ranked[0].story.id == "specific"


def test_legal_solicitation_is_penalized_and_capped():
    legal1 = mk_story("l1", ["e1"], impact=0.80, title="Class action deadline for AAA")
    legal2 = mk_story("l2", ["e2"], impact=0.79, title="Lead plaintiff alert for BBB")
    normal = mk_story("n1", ["e3"], impact=0.60, title="Micron earnings beat")
    result = mk_result(
        [legal1, legal2, normal],
        [
            mk_event("e1", "Rosen law firm announces class action deadline"),
            mk_event("e2", "Lead plaintiff deadline reminder"),
            mk_event("e3", "Micron earnings beat"),
        ],
    )
    ranked = rank_final(
        [legal1, legal2, normal],
        result,
        FinalRankConfig(top_n=3, max_legal_solicitations=1),
    )
    assert is_legal_solicitation(legal1, result)
    assert sum(1 for item in ranked if item.story.id.startswith("l")) == 1
    assert any(item.story.id == "n1" for item in ranked)


def test_legit_deadline_not_flagged_as_solicitation():
    """정당한 금융 뉴스의 'deadline'(규제·공개매수·만기)은 로펌광고로 오탐 금지."""
    result = mk_result([], [])
    for title in [
        "SEC filing deadline extended for NVDA merger vote",
        "Tender offer deadline is Friday for the buyout",
        "Debt maturity deadline looms for the issuer",
        "US government shutdown deadline approaches",
    ]:
        story = mk_story("s", ["e1"], impact=0.5, title=title)
        assert not is_legal_solicitation(story, result), title


def test_legit_reported_losses_not_flagged():
    """실적 뉴스의 'reported losses of $X'는 로펌광고로 오탐 금지."""
    result = mk_result([], [])
    for title in [
        "Micron reported net losses of $2 billion last quarter",
        "Startup posts operating losses of $50 million",
    ]:
        story = mk_story("s", ["e1"], impact=0.5, title=title)
        assert not is_legal_solicitation(story, result), title


def test_legal_deadline_reminder_still_flagged():
    """로펌광고 문맥의 'deadline reminder'·'losses of more than'은 계속 탐지."""
    result = mk_result([], [])
    reminder = mk_story("s1", ["e1"], impact=0.5,
                        title="Deadline reminder for affected investors")
    assert is_legal_solicitation(reminder, result)

    big_loss = mk_story("s2", ["e2"], impact=0.5,
                        title="Investors with losses of more than $100,000 should act")
    assert is_legal_solicitation(big_loss, result)


def test_bare_deadline_no_longer_penalizes_ranking():
    """'deadline' 단독 뉴스가 legal 페널티/cap 없이 정상 랭크되는지(엔드투엔드)."""
    normal = mk_story("n", ["e1"], impact=0.60,
                      title="Tender offer deadline set for NVDA acquisition")
    result = mk_result([normal], [mk_event("e1", "Tender offer deadline Friday")])
    ranked = rank_final([normal], result)
    assert len(ranked) == 1
    # legal 페널티(-0.25) 미적용 → impact 그대로(보너스 없으면 동일)
    assert ranked[0].final_score >= 0.60


def test_primary_ticker_diversity_cap():
    stories = [
        mk_story("a", ["e1"], impact=0.90, tickers=("NVDA",)),
        mk_story("b", ["e2"], impact=0.80, tickers=("NVDA",)),
        mk_story("c", ["e3"], impact=0.70, tickers=("NVDA",)),
        mk_story("d", ["e4"], impact=0.60, tickers=("MSFT",)),
    ]
    result = mk_result(stories, [mk_event(f"e{i}") for i in range(1, 5)])
    ranked = rank_final(stories, result, FinalRankConfig(top_n=4, max_per_primary_ticker=2))
    ids = [item.story.id for item in ranked]
    assert ids == ["a", "b", "d"]
