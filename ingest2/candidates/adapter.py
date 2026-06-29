"""EventCluster → src.ingest.schema.Event 어댑터.

§6 산출물(EventCluster)을 §7이 재사용하는 src/causal·src/research의 입력 단위
(Event)로 변환한다. 결정 사항(D): 인과 후보 비교 시 간접 티커도 포함한다
(include_indirect=True) — 1·2차 파급 연결을 놓치지 않기 위해.
"""
from __future__ import annotations

from datetime import UTC, datetime

from src.ingest.schema import Event

from ..schema import EventCluster


def _uniq_keep(seq) -> list[str]:
    out: list[str] = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def cluster_to_event(cluster: EventCluster, *, include_indirect: bool = True) -> Event:
    """EventCluster 1개 → Event 1개.

    - occurred_at: published_start(최조기) → published_end → now 순으로 폴백.
      (Event.occurred_at은 None 불가)
    - tickers_mentioned: 직접 티커 + (옵션) 간접 티커.
    """
    occurred = (
        cluster.published_start
        or cluster.published_end
        or datetime.now(UTC)
    )

    tickers = list(cluster.tickers_direct)
    if include_indirect:
        tickers = _uniq_keep([*tickers, *cluster.tickers_indirect])

    return Event(
        id=cluster.cluster_id,
        title=cluster.title,
        summary=cluster.summary or cluster.title,
        occurred_at=occurred,
        source_urls=list(cluster.urls),
        publishers=list(cluster.source_ids),
        tickers_mentioned=tickers,
        spread=cluster.spread,
    )


def clusters_to_events(
    clusters: list[EventCluster], *, include_indirect: bool = True
) -> list[Event]:
    return [cluster_to_event(c, include_indirect=include_indirect) for c in clusters]
