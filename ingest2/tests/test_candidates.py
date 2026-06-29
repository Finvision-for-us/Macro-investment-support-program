"""§7 후보 생성 — 어댑터·사전점수·파이프라인. 외부 호출은 전부 가짜 주입(오프라인)."""
from __future__ import annotations

import numpy as np

from ingest2.candidates.adapter import cluster_to_event, clusters_to_events
from ingest2.candidates.pipeline import (
    CandidateConfig,
    generate_candidates,
)
from ingest2.candidates.prescore import prescore, top_k
from ingest2.schema import EventCluster
from src.causal.schema import CausalEdge
from src.research.schema import DeepReport, ShallowReport


# ---------------- helpers ----------------
def mk_cluster(
    cid: str,
    tickers,
    *,
    indirect=(),
    spread=1,
    tier=3,
    etypes=("earnings",),
) -> EventCluster:
    return EventCluster(
        cluster_id=cid,
        member_ids=[f"{cid}:1"],
        representative_id=f"{cid}:1",
        title=f"{cid} headline",
        summary=f"{cid} summary body text",
        tickers_direct=list(tickers),
        tickers_indirect=list(indirect),
        event_types=list(etypes),
        source_ids=["rss_x"],
        urls=[f"http://x/{cid}"],
        trust_tier_best=tier,
        spread=spread,
    )


def fake_embed(events):
    return np.ones((len(events), 4), dtype=np.float32)


def fake_pairwise(events, embeddings, *, on_progress=None):
    """티커를 공유하는 첫 쌍을 인과로 연결(스토리 1개 형성)."""
    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            if set(events[i].tickers_mentioned) & set(events[j].tickers_mentioned):
                return [
                    CausalEdge(
                        from_event_id=events[i].id,
                        to_event_id=events[j].id,
                        confidence=0.8,
                        direction="positive",
                        mechanism="fake",
                        source_urls=[],
                        inferred_by="pairwise_llm",
                    )
                ]
    return []


def fake_shallow(event) -> ShallowReport:
    return ShallowReport(
        event_id=event.id, background="bg", direction="positive", confidence=0.5
    )


def make_fake_deep(record: list):
    def fake_deep(event, shallow):
        record.append(event.id)
        return DeepReport(event_id=event.id, direction="positive", confidence=0.6), []

    return fake_deep


def fake_claims(events, embeddings, deep_reports, *, on_progress=None):
    return []


def fake_narrative(story, events_by_id, deep_reports):
    return story.model_copy(update={"title": f"title:{len(story.event_ids)}"})


def _run(clusters, config=None, deep_record=None):
    return generate_candidates(
        clusters,
        config or CandidateConfig(),
        embed_fn=fake_embed,
        pairwise_fn=fake_pairwise,
        shallow_fn=fake_shallow,
        deep_fn=make_fake_deep(deep_record if deep_record is not None else []),
        claims_fn=fake_claims,
        narrative_fn=fake_narrative,
        on_log=lambda m: None,
    )


# ---------------- adapter ----------------
def test_adapter_maps_core_fields():
    c = mk_cluster("c1", ["AAA", "BBB"], indirect=["CCC"], spread=3)
    ev = cluster_to_event(c)
    assert ev.id == "c1"
    assert ev.title == "c1 headline"
    assert ev.spread == 3
    assert ev.source_urls == ["http://x/c1"]
    # 간접 티커 포함 (기본)
    assert ev.tickers_mentioned == ["AAA", "BBB", "CCC"]


def test_adapter_can_exclude_indirect():
    c = mk_cluster("c1", ["AAA"], indirect=["CCC"])
    ev = cluster_to_event(c, include_indirect=False)
    assert ev.tickers_mentioned == ["AAA"]


def test_adapter_occurred_at_falls_back_to_now():
    c = mk_cluster("c1", ["AAA"])  # published_start/end 모두 None
    ev = cluster_to_event(c)
    assert ev.occurred_at is not None


def test_clusters_to_events_count():
    cs = [mk_cluster(f"c{i}", ["AAA"]) for i in range(3)]
    assert len(clusters_to_events(cs)) == 3


# ---------------- prescore ----------------
def test_prescore_rewards_spread_and_tier():
    low = mk_cluster("low", ["AAA"], spread=1, tier=3, etypes=("other",))
    high = mk_cluster("high", ["AAA", "BBB"], spread=8, tier=1, etypes=("m_and_a",))
    assert prescore(high) > prescore(low)


def test_top_k_truncates_and_sorts():
    cs = [
        mk_cluster("a", ["X"], spread=1, etypes=("other",)),
        mk_cluster("b", ["X"], spread=8, etypes=("m_and_a",)),
        mk_cluster("c", ["X"], spread=4, etypes=("earnings",)),
    ]
    ranked = top_k(cs, 2)
    assert len(ranked) == 2
    assert ranked[0][0].cluster_id == "b"  # 가장 높은 점수


# ---------------- pipeline ----------------
def test_empty_clusters():
    res = _run([])
    assert res.stories == []
    assert res.stats["clusters_in"] == 0


def test_signals_and_stories_in_one_pool():
    clusters = [
        mk_cluster("s1", ["AAA"], spread=3),       # 스토리 멤버
        mk_cluster("s2", ["AAA"], spread=3),       # 스토리 멤버 (AAA 공유)
        mk_cluster("g1", ["BBB"], spread=5),       # 시그널
        mk_cluster("g2", ["CCC"], spread=1),       # 시그널
    ]
    res = _run(clusters)
    # 4 이벤트 → 1 스토리(2) + 2 시그널 = 3 컴포넌트
    assert res.stats["components"] == 3
    assert res.stats["stories"] == 1
    assert res.stats["signals"] == 2
    assert len(res.stories) == 3
    # 한 바구니: signals + multi_stories == stories
    assert len(res.signals) + len(res.multi_stories) == len(res.stories)


def test_narrative_applied():
    clusters = [mk_cluster("g1", ["BBB"]), mk_cluster("g2", ["CCC"])]
    res = _run(clusters)
    assert all(s.title.startswith("title:") for s in res.stories)


def test_narrate_false_skips_titles():
    clusters = [mk_cluster("g1", ["BBB"]), mk_cluster("g2", ["CCC"])]
    res = _run(clusters, CandidateConfig(narrate=False))
    assert all(s.title == "" for s in res.stories)


def test_deep_targets_story_plus_high_value_signal():
    clusters = [
        mk_cluster("s1", ["AAA"], spread=3),       # 스토리
        mk_cluster("s2", ["AAA"], spread=3),       # 스토리
        mk_cluster("g1", ["BBB"], spread=8),       # 고가치 시그널 (높은 spread)
        mk_cluster("g2", ["CCC"], spread=1),       # 저가치 시그널
    ]
    record: list[str] = []
    res = _run(clusters, CandidateConfig(deep_high_value_signals=1), deep_record=record)
    deep_ids = set(record)
    # 스토리 두 이벤트는 반드시 포함
    assert {"s1", "s2"} <= deep_ids
    # 고가치 시그널 g1 포함, 저가치 g2 제외
    assert "g1" in deep_ids
    assert "g2" not in deep_ids
    assert res.stats["deep"] == 3


def test_max_deep_caps_calls():
    clusters = [mk_cluster(f"s{i}", ["AAA"], spread=3) for i in range(6)]
    record: list[str] = []
    _run(clusters, CandidateConfig(max_deep=2), deep_record=record)
    assert len(record) == 2


def test_no_story_no_high_value_signal_means_no_deep():
    clusters = [mk_cluster("g1", ["BBB"]), mk_cluster("g2", ["CCC"])]
    record: list[str] = []
    res = _run(clusters, CandidateConfig(deep_high_value_signals=0), deep_record=record)
    assert record == []
    assert res.stats["deep"] == 0
    # 그래도 시그널 후보는 생성됨
    assert res.stats["signals"] == 2
