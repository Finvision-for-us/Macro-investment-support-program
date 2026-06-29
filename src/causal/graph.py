"""인과 edge → DiGraph → 연결 컴포넌트(=Story 후보) → Story 스켈레톤.

Day 6~8 (story.py) 에서 narrative_short / narrative_long 을 채움.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

import networkx as nx

from src.causal.schema import CausalEdge, Direction, Story
from src.ingest.schema import Event

MIN_STORY_SIZE = 1
MAX_STORY_SIZE = 20


@dataclass
class StoryCandidate:
    """그래프 컴포넌트 + 메타."""

    event_ids: list[str]
    edges: list[CausalEdge] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.event_ids)

    @property
    def is_isolated(self) -> bool:
        return self.size == 1 and not self.edges


def build_graph(events: list[Event], edges: list[CausalEdge]) -> nx.DiGraph:
    """이벤트 노드 + 인과 edge로 directed graph 빌드."""
    g: nx.DiGraph = nx.DiGraph()
    for ev in events:
        g.add_node(ev.id, event=ev)
    for e in edges:
        if e.from_event_id in g and e.to_event_id in g:
            g.add_edge(e.from_event_id, e.to_event_id, edge=e)
    return g


def extract_components(graph: nx.DiGraph) -> list[StoryCandidate]:
    """약한 연결 컴포넌트 (방향 무시) 추출. 단일 노드 포함."""
    out: list[StoryCandidate] = []
    for nodeset in nx.weakly_connected_components(graph):
        nodes = list(nodeset)
        subg = graph.subgraph(nodes)
        edges_in_comp = [d["edge"] for _, _, d in subg.edges(data=True) if "edge" in d]
        out.append(StoryCandidate(event_ids=nodes, edges=edges_in_comp))
    return out


def filter_size(
    components: list[StoryCandidate],
    min_size: int = MIN_STORY_SIZE,
    max_size: int = MAX_STORY_SIZE,
) -> list[StoryCandidate]:
    return [c for c in components if min_size <= c.size <= max_size]


def _story_id(event_ids: list[str]) -> str:
    raw = "|".join(sorted(event_ids))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _aggregate_direction(edges: list[CausalEdge]) -> Direction:
    """edge direction 다수결. 동률 → uncertain."""
    if not edges:
        return "uncertain"
    counts = Counter(e.direction for e in edges)
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return "uncertain"
    return top[0][0]


def _aggregate_confidence(edges: list[CausalEdge]) -> float:
    if not edges:
        return 0.5
    return sum(e.confidence for e in edges) / len(edges)


def build_story_skeletons(
    components: list[StoryCandidate],
    events_by_id: dict[str, Event],
    scored_by_id: dict[str, dict],
) -> list[Story]:
    """Day 4~5 산출물: narrative 없는 Story 스켈레톤. 영향력 내림차순 정렬."""
    stories: list[Story] = []
    for comp in components:
        # 종목 합집합
        tickers: set[str] = set()
        impact_sum = 0.0
        all_sources: list[str] = []
        seen_src: set[str] = set()
        for eid in comp.event_ids:
            ev = events_by_id.get(eid)
            if ev:
                tickers.update(ev.tickers_mentioned)
                for u in ev.source_urls:
                    if u not in seen_src:
                        seen_src.add(u)
                        all_sources.append(u)
            sc = scored_by_id.get(eid)
            if sc:
                impact_sum += float(sc.get("impact_score", 0.0) or 0.0)

        stories.append(
            Story(
                id=_story_id(comp.event_ids),
                event_ids=list(comp.event_ids),
                direction=_aggregate_direction(comp.edges),
                confidence=_aggregate_confidence(comp.edges),
                affected_tickers=sorted(tickers),
                aggregated_impact=impact_sum,
                edges=comp.edges,
                all_sources=all_sources,
            )
        )

    stories.sort(key=lambda s: -s.aggregated_impact)
    return stories
