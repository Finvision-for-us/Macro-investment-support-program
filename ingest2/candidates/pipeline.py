"""§7 오케스트레이션 — EventCluster 목록 → 시그널/스토리 후보 한 바구니.

흐름(그림 §7):
  1. 사전점수 → Top-K
  2. 어댑터 → Event
  3. 임베딩
  4. 1차 인과 edge (pairwise)
  5. 컴포넌트 분리 → 시그널/스토리 (딥 타깃 선정용)
  6. 얕은 리서치 — Top-K 전반
  7. 깊은 리서치 — 스토리 + 고가치 시그널 (비용 가드)
  8. claim 기반 edge 재발굴
  9. edge 병합 → 재그룹
 10. 스토리 스켈레톤 (사전점수를 임시 impact로)
 11. 내러티브 생성

src/causal·src/research를 그대로 재사용한다. 모든 외부 호출(임베딩·인과·리서치·
내러티브)은 주입 가능 — 기본값은 실제 src 함수, 테스트는 가짜를 주입해 오프라인 실행.
무거운 의존(tavily/parallel)은 리서치 기본값에서만 지연 import 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.causal.edges import (
    event_embeddings as _event_embeddings,
)
from src.causal.edges import (
    infer_from_claims as _infer_from_claims,
)
from src.causal.edges import (
    infer_pairwise as _infer_pairwise,
)
from src.causal.edges import (
    merge_edges,
)
from src.causal.graph import (
    build_graph,
    build_story_skeletons,
    extract_components,
)
from src.causal.schema import CausalEdge, Story
from src.causal.story import generate_narrative as _generate_narrative
from src.ingest.schema import Event
from src.research.schema import ShallowReport

from ..schema import EventCluster
from .adapter import clusters_to_events
from .prescore import top_k as _top_k


@dataclass
class CandidateConfig:
    top_k: int = 25                 # 비싼 단계로 넘길 상위 후보 수
    include_indirect: bool = True   # 인과 후보 비교에 간접 티커 포함
    max_deep: int = 10              # 깊은 리서치 최대 건수 (비용 상한)
    deep_high_value_signals: int = 3  # 스토리 외 추가로 깊은 리서치할 고가치 시그널 수
    narrate: bool = True            # 내러티브(제목/요약) 생성 여부
    respect_cost_guard: bool = True  # Parallel 잔여 크레딧 부족 시 deep 자동 skip


@dataclass
class CandidateResult:
    stories: list[Story]                       # 한 바구니 (시그널 = 1-이벤트 스토리)
    events_by_id: dict[str, Event]
    edges: list[CausalEdge]
    shallow_reports: dict[str, ShallowReport]
    deep_reports: dict[str, dict]              # event_id → DeepReport.model_dump()
    prescore_by_id: dict[str, float]
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def signals(self) -> list[Story]:
        """단일 사건 후보."""
        return [s for s in self.stories if len(s.event_ids) == 1]

    @property
    def multi_stories(self) -> list[Story]:
        """다중 사건(인과 체인) 후보."""
        return [s for s in self.stories if len(s.event_ids) > 1]


# ---- 리서치 기본값: tavily/parallel 지연 import (모듈 import 안전) ----
def _default_shallow(event: Event) -> ShallowReport:
    from src.research.shallow import shallow_research

    return shallow_research(event)


def _default_deep(event: Event, shallow: ShallowReport):
    from src.research.deep import deep_research

    return deep_research(event, shallow)


def _cost_guard_status() -> tuple[bool, str | None]:
    try:
        from src.cost_guard import should_skip_deep, warn_message

        return should_skip_deep(), warn_message()
    except Exception:  # noqa: BLE001 — 가드 실패가 본 작업을 깨면 안 됨
        return False, None


def _select_deep_targets(
    story_event_ids: set[str],
    signal_event_ids: list[str],
    prescore_by_id: dict[str, float],
    config: CandidateConfig,
) -> list[str]:
    """깊은 리서치 대상 = 스토리 이벤트 전부 + 고가치 시그널 top-N, max_deep 캡."""
    def score(eid: str) -> float:
        return prescore_by_id.get(eid, 0.0)

    high_value_signals = sorted(signal_event_ids, key=score, reverse=True)[
        : config.deep_high_value_signals
    ]

    ordered: list[str] = []
    seen: set[str] = set()
    for eid in (*story_event_ids, *high_value_signals):
        if eid not in seen:
            seen.add(eid)
            ordered.append(eid)

    ordered.sort(key=score, reverse=True)
    return ordered[: config.max_deep]


def generate_candidates(
    clusters: list[EventCluster],
    config: CandidateConfig | None = None,
    *,
    embed_fn=None,
    pairwise_fn=None,
    shallow_fn=None,
    deep_fn=None,
    claims_fn=None,
    narrative_fn=None,
    on_log=print,
) -> CandidateResult:
    """EventCluster 목록 → 시그널/스토리 후보."""
    config = config or CandidateConfig()
    embed_fn = embed_fn or _event_embeddings
    pairwise_fn = pairwise_fn or _infer_pairwise
    claims_fn = claims_fn or _infer_from_claims
    narrative_fn = narrative_fn or _generate_narrative
    shallow_fn = shallow_fn or _default_shallow
    deep_fn = deep_fn or _default_deep

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    if not clusters:
        return CandidateResult([], {}, [], {}, {}, {}, {"clusters_in": 0})

    # 1) 사전점수 → Top-K
    scored = _top_k(clusters, config.top_k)
    sel_clusters = [c for c, _ in scored]
    prescore_by_id = {c.cluster_id: s for c, s in scored}

    # 2) 어댑터 → Event
    events = clusters_to_events(sel_clusters, include_indirect=config.include_indirect)
    events_by_id = {e.id: e for e in events}

    # 3) 임베딩 (pairwise·claim 두 단계가 공유)
    emb = embed_fn(events)

    # 4) 1차 인과 edge
    log(f"[pairwise] {len(events)} events")
    edges1 = pairwise_fn(events, emb)

    # 5) 1차 컴포넌트 → 시그널/스토리 분리 (딥 타깃 선정용)
    comp1 = extract_components(build_graph(events, edges1))
    story_event_ids: set[str] = set()
    signal_event_ids: list[str] = []
    for comp in comp1:
        if comp.size > 1:
            story_event_ids.update(comp.event_ids)
        else:
            signal_event_ids.append(comp.event_ids[0])

    # 6) 얕은 리서치 — Top-K 전반
    shallow_reports: dict[str, ShallowReport] = {}
    for e in events:
        try:
            shallow_reports[e.id] = shallow_fn(e)
        except Exception as ex:  # noqa: BLE001
            log(f"[shallow:err] {e.id[:8]} {str(ex)[:80]}")

    # 7) 깊은 리서치 — 스토리 + 고가치 시그널
    deep_targets = _select_deep_targets(
        story_event_ids, signal_event_ids, prescore_by_id, config
    )
    if config.respect_cost_guard and deep_targets:
        skip, warn = _cost_guard_status()
        if warn:
            log(f"[cost] {warn}")
        if skip:
            log("[cost] 잔여 크레딧 부족 → deep research skip")
            deep_targets = []

    deep_reports: dict[str, dict] = {}
    for eid in deep_targets:
        e = events_by_id.get(eid)
        sh = shallow_reports.get(eid)
        if e is None or sh is None:
            continue
        try:
            report, _evidence = deep_fn(e, sh)
            deep_reports[eid] = report.model_dump()
        except Exception as ex:  # noqa: BLE001
            log(f"[deep:err] {eid[:8]} {str(ex)[:80]}")

    # 8) claim 기반 edge 재발굴
    edges2: list[CausalEdge] = []
    if deep_reports:
        log(f"[claims] from {len(deep_reports)} deep reports")
        edges2 = claims_fn(events, emb, deep_reports)

    # 9) 병합 → 재그룹
    edges = merge_edges([*edges1, *edges2])
    comps = extract_components(build_graph(events, edges))

    # 10) 스토리 스켈레톤 — 사전점수를 임시 impact로 (§8에서 정밀 스코어로 교체)
    scored_by_id = {
        eid: {"impact_score": prescore_by_id.get(eid, 0.0)} for eid in events_by_id
    }
    stories = build_story_skeletons(comps, events_by_id, scored_by_id)

    # 11) 내러티브
    if config.narrate:
        stories = [narrative_fn(s, events_by_id, deep_reports) for s in stories]

    stats = {
        "clusters_in": len(clusters),
        "top_k": len(sel_clusters),
        "edges_pairwise": len(edges1),
        "shallow": len(shallow_reports),
        "deep": len(deep_reports),
        "edges_claims": len(edges2),
        "edges": len(edges),
        "components": len(comps),
        "signals": sum(1 for s in stories if len(s.event_ids) == 1),
        "stories": sum(1 for s in stories if len(s.event_ids) > 1),
    }

    return CandidateResult(
        stories=stories,
        events_by_id=events_by_id,
        edges=edges,
        shallow_reports=shallow_reports,
        deep_reports=deep_reports,
        prescore_by_id=prescore_by_id,
        stats=stats,
    )


def candidates_from_store(
    news_store,
    config: CandidateConfig | None = None,
    *,
    dedup_embedder=None,
    **kwargs,
) -> CandidateResult:
    """편의: 저장소의 통과 항목을 중복제거 → 후보 생성까지 한 번에."""
    from ..dedup.cluster import dedup_passed

    clusters = dedup_passed(news_store, embedder=dedup_embedder)
    return generate_candidates(clusters, config, **kwargs)
