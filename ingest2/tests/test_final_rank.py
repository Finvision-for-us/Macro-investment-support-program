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
