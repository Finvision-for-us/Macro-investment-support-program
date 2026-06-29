"""M2 graph + 컴포넌트 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timezone

from src.causal.graph import (
    build_graph,
    build_story_skeletons,
    extract_components,
    filter_size,
)
from src.causal.schema import CausalEdge
from src.ingest.schema import Event


def _ev(idx: int, tickers: list[str] | None = None) -> Event:
    return Event(
        id=f"e{idx}",
        title=f"Event {idx}",
        summary="s",
        occurred_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        source_urls=[f"http://x.com/{idx}"],
        publishers=["p"],
        tickers_mentioned=tickers or [f"T{idx}"],
        spread=2,
    )


def _edge(a: str, b: str, conf: float = 0.7, direction: str = "positive") -> CausalEdge:
    return CausalEdge(
        from_event_id=a,
        to_event_id=b,
        confidence=conf,
        direction=direction,
        mechanism="m",
        inferred_by="pairwise_llm",
    )


def test_build_graph_nodes_and_edges():
    events = [_ev(1), _ev(2), _ev(3)]
    edges = [_edge("e1", "e2"), _edge("e2", "e3")]
    g = build_graph(events, edges)
    assert g.number_of_nodes() == 3
    assert g.number_of_edges() == 2


def test_build_graph_skips_edges_with_missing_endpoint():
    events = [_ev(1), _ev(2)]
    edges = [_edge("e1", "e99")]  # e99 없음
    g = build_graph(events, edges)
    assert g.number_of_edges() == 0


def test_extract_components_chains_into_one():
    events = [_ev(1), _ev(2), _ev(3), _ev(4)]
    edges = [_edge("e1", "e2"), _edge("e2", "e3")]  # e4는 고립
    g = build_graph(events, edges)
    comps = extract_components(g)
    sizes = sorted(c.size for c in comps)
    assert sizes == [1, 3]


def test_filter_size_drops_too_small_and_too_big():
    events = [_ev(i) for i in range(1, 5)]
    edges = [_edge(f"e{i}", f"e{i+1}") for i in range(1, 4)]
    g = build_graph(events, edges)
    comps = extract_components(g)
    # 모두 size=4, filter min=2, max=10
    filtered = filter_size(comps, min_size=2, max_size=10)
    assert len(filtered) == 1


def test_build_story_skeletons_aggregates_tickers_and_impact():
    events = [_ev(1, ["NVDA"]), _ev(2, ["AMD"]), _ev(3, ["NVDA", "AMD"])]
    edges = [_edge("e1", "e3"), _edge("e2", "e3", direction="negative")]
    g = build_graph(events, edges)
    comps = extract_components(g)
    events_by_id = {ev.id: ev for ev in events}
    scored_by_id = {
        "e1": {"impact_score": 0.5},
        "e2": {"impact_score": 0.3},
        "e3": {"impact_score": 0.8},
    }
    stories = build_story_skeletons(comps, events_by_id, scored_by_id)
    assert len(stories) == 1
    s = stories[0]
    assert sorted(s.affected_tickers) == ["AMD", "NVDA"]
    assert abs(s.aggregated_impact - 1.6) < 1e-6
    # direction: 1 positive + 1 negative → uncertain (동률)
    assert s.direction == "uncertain"


def test_build_story_skeletons_isolated_node():
    events = [_ev(1, ["NVDA"])]
    g = build_graph(events, [])
    comps = extract_components(g)
    stories = build_story_skeletons(
        comps, {"e1": events[0]}, {"e1": {"impact_score": 0.4}}
    )
    assert len(stories) == 1
    assert stories[0].event_ids == ["e1"]
    assert stories[0].direction == "uncertain"
