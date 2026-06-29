"""§8 AI 분석층 — 영향도 스코어·방향·신뢰도. LLM은 가짜 주입(오프라인)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ingest2.analyze.score import (
    ImpactAnalysis,
    _build_prompt,
    analyze_story,
    score_candidates,
)
from ingest2.candidates.pipeline import CandidateResult
from src.causal.schema import CausalEdge, Story
from src.ingest.schema import Event
from src.research.schema import DeepReport, ShallowReport

# ---- helpers ----

def _now():
    return datetime.now(UTC)


def mk_event(eid: str, tickers=("AAA",), title: str = "") -> Event:
    return Event(
        id=eid,
        title=title or f"{eid} headline",
        summary=f"{eid} summary",
        occurred_at=_now(),
        source_urls=[f"http://x/{eid}"],
        publishers=["test"],
        tickers_mentioned=list(tickers),
        spread=3,
    )


def mk_story(sid: str, event_ids: list[str], *, impact=0.5, direction="uncertain") -> Story:
    return Story(
        id=sid,
        event_ids=event_ids,
        direction=direction,
        confidence=0.5,
        affected_tickers=["AAA"],
        aggregated_impact=impact,
    )


def fake_llm(impact: float, direction: str = "positive", confidence: float = 0.8):
    def _llm(prompt: str) -> ImpactAnalysis:
        return ImpactAnalysis(
            impact_score=impact,
            direction=direction,
            confidence=confidence,
            rationale="test",
        )
    return _llm


def mk_result(stories: list[Story], events_by_id=None) -> CandidateResult:
    return CandidateResult(
        stories=stories,
        events_by_id=events_by_id or {},
        edges=[],
        shallow_reports={},
        deep_reports={},
        prescore_by_id={},
        stats={},
    )


# ---- ImpactAnalysis 스키마 ----

def test_impact_analysis_validates():
    a = ImpactAnalysis(impact_score=0.7, direction="positive", confidence=0.85)
    assert a.impact_score == 0.7
    assert a.direction == "positive"


def test_impact_analysis_clamps_range():
    with pytest.raises(ValidationError):
        ImpactAnalysis(impact_score=1.5, direction="positive", confidence=0.5)


def test_impact_analysis_rationale_optional():
    a = ImpactAnalysis(impact_score=0.3, direction="uncertain", confidence=0.6)
    assert a.rationale == ""


# ---- _build_prompt ----

def test_build_prompt_signal_header():
    story = mk_story("s1", ["e1"])
    ev = mk_event("e1", title="Micron earnings beat")
    prompt = _build_prompt(story, {"e1": ev}, {}, {})
    assert "[시그널" in prompt
    assert "Micron" in prompt


def test_build_prompt_story_header():
    story = mk_story("s1", ["e1", "e2"])
    evs = {"e1": mk_event("e1"), "e2": mk_event("e2")}
    prompt = _build_prompt(story, evs, {}, {})
    assert "[스토리" in prompt
    assert "이벤트 2개" in prompt


def test_build_prompt_includes_shallow_background():
    story = mk_story("s1", ["e1"])
    ev = mk_event("e1")
    sh = ShallowReport(
        event_id="e1",
        background="AI chip demand surge",
        direction="positive",
        confidence=0.7,
    )
    prompt = _build_prompt(story, {"e1": ev}, {"e1": sh}, {})
    assert "AI chip demand" in prompt


def test_build_prompt_includes_deep_report():
    story = mk_story("s1", ["e1"])
    ev = mk_event("e1")
    dr = DeepReport(
        event_id="e1",
        direction="positive",
        confidence=0.9,
        direct_causes=[{"claim": "Fed rate cut expected", "source_urls": []}],
    )
    prompt = _build_prompt(story, {"e1": ev}, {}, {"e1": dr.model_dump()})
    assert "Fed rate cut" in prompt


def test_build_prompt_includes_edge_mechanism():
    edge = CausalEdge(
        from_event_id="e1", to_event_id="e2",
        confidence=0.75, direction="positive",
        mechanism="supply chain disruption", source_urls=[], inferred_by="pairwise_llm",
    )
    story = mk_story("s1", ["e1", "e2"])
    story = story.model_copy(update={"edges": [edge]})
    evs = {"e1": mk_event("e1"), "e2": mk_event("e2")}
    prompt = _build_prompt(story, evs, {}, {})
    assert "supply chain" in prompt


def test_build_prompt_truncates_tickers():
    tickers = [f"T{i:02d}" for i in range(20)]
    story = mk_story("s1", ["e1"])
    story = story.model_copy(update={"affected_tickers": tickers})
    ev = mk_event("e1")
    prompt = _build_prompt(story, {"e1": ev}, {}, {})
    # 12개 이하만
    shown = [t for t in tickers if t in prompt]
    assert len(shown) <= 12


# ---- analyze_story ----

def test_analyze_story_updates_impact():
    story = mk_story("s1", ["e1"], impact=0.1, direction="uncertain")
    updated = analyze_story(story, {}, {}, {}, llm_fn=fake_llm(0.75, "positive", 0.9))
    assert updated.aggregated_impact == 0.75
    assert updated.direction == "positive"
    assert updated.confidence == 0.9


def test_analyze_story_preserves_event_ids():
    story = mk_story("s1", ["e1", "e2"], impact=0.2)
    updated = analyze_story(story, {}, {}, {}, llm_fn=fake_llm(0.6))
    assert updated.event_ids == ["e1", "e2"]
    assert updated.id == "s1"


def test_analyze_story_preserves_edges():
    edge = CausalEdge(
        from_event_id="e1", to_event_id="e2",
        confidence=0.8, direction="positive",
        mechanism="m", source_urls=[], inferred_by="pairwise_llm",
    )
    story = mk_story("s1", ["e1", "e2"])
    story = story.model_copy(update={"edges": [edge]})
    updated = analyze_story(story, {}, {}, {}, llm_fn=fake_llm(0.7))
    assert len(updated.edges) == 1


def test_signal_direction_updated_from_uncertain():
    story = mk_story("s1", ["e1"], direction="uncertain")
    updated = analyze_story(story, {}, {}, {}, llm_fn=fake_llm(0.5, "negative", 0.7))
    assert updated.direction == "negative"


# ---- score_candidates ----

def test_score_candidates_all_scored():
    stories = [mk_story(f"s{i}", [f"e{i}"], impact=float(i) * 0.1) for i in range(4)]
    result = mk_result(stories)
    scored = score_candidates(result, llm_fn=fake_llm(0.6), on_log=lambda m: None)
    assert len(scored) == 4
    assert all(s.aggregated_impact == 0.6 for s in scored)


def test_score_candidates_sorted_descending():
    call_n = [0]

    def varying_llm(prompt: str) -> ImpactAnalysis:
        call_n[0] += 1
        return ImpactAnalysis(
            impact_score=1.0 - call_n[0] * 0.2,
            direction="positive",
            confidence=0.8,
        )

    stories = [mk_story(f"s{i}", [f"e{i}"]) for i in range(4)]
    result = mk_result(stories)
    scored = score_candidates(result, llm_fn=varying_llm, on_log=lambda m: None)
    impacts = [s.aggregated_impact for s in scored]
    assert impacts == sorted(impacts, reverse=True)


def test_score_candidates_error_keeps_original():
    def bad_llm(prompt: str) -> ImpactAnalysis:
        raise RuntimeError("API down")

    story = mk_story("s1", ["e1"], impact=0.42)
    result = mk_result([story])
    scored = score_candidates(result, llm_fn=bad_llm, on_log=lambda m: None)
    assert len(scored) == 1
    assert scored[0].aggregated_impact == pytest.approx(0.42)


def test_score_candidates_empty():
    result = mk_result([])
    scored = score_candidates(result, llm_fn=fake_llm(0.5), on_log=lambda m: None)
    assert scored == []


def test_score_candidates_uses_shallow_and_deep():
    """prompt에 shallow·deep 내용이 전달되는지 llm이 받는 prompt 검증."""
    received: list[str] = []

    def capture_llm(prompt: str) -> ImpactAnalysis:
        received.append(prompt)
        return ImpactAnalysis(impact_score=0.5, direction="positive", confidence=0.7)

    ev = mk_event("e1", title="NVDA earnings beat")
    sh = ShallowReport(
        event_id="e1",
        background="strong AI demand",
        direction="positive",
        confidence=0.8,
    )
    dr = DeepReport(
        event_id="e1", direction="positive", confidence=0.9,
        direct_causes=[{"claim": "data center orders tripled", "source_urls": []}],
    )
    story = mk_story("s1", ["e1"])
    story = story.model_copy(update={"affected_tickers": ["NVDA"]})

    result = CandidateResult(
        stories=[story],
        events_by_id={"e1": ev},
        edges=[],
        shallow_reports={"e1": sh},
        deep_reports={"e1": dr.model_dump()},
        prescore_by_id={},
        stats={},
    )
    score_candidates(result, llm_fn=capture_llm, on_log=lambda m: None)
    assert received, "llm not called"
    assert "strong AI demand" in received[0]
    assert "data center orders tripled" in received[0]
