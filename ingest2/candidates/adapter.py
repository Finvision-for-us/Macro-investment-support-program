"""EventCluster → src.ingest.schema.Event 어댑터.

§6 산출물(EventCluster)을 §7이 재사용하는 src/causal·src/research의 입력 단위
(Event)로 변환한다. 결정 사항(D): 간접(파급) 티커를 보존하되 직접 티커와
분리해 담는다 — Event.tickers_mentioned(직접)와 Event.tickers_indirect(파급)는
하류에서 용도가 다르다(price_reaction·causal.edges는 직접 티커만 사용). include_indirect
=True면 파급 티커를 tickers_indirect에 채우고, False면 비운다.
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
    - tickers_mentioned: 직접 언급 티커만. tickers_indirect: 파급 티커(옵션).
    """
    occurred = (
        cluster.published_start
        or cluster.published_end
        or datetime.now(UTC)
    )

    direct = _uniq_keep(cluster.tickers_direct)
    indirect = _uniq_keep(
        [t for t in cluster.tickers_indirect if t not in direct]
    ) if include_indirect else []

    return Event(
        id=cluster.cluster_id,
        title=cluster.title,
        summary=cluster.summary or cluster.title,
        occurred_at=occurred,
        source_urls=list(cluster.urls),
        publishers=list(cluster.source_ids),
        tickers_mentioned=direct,
        tickers_indirect=indirect,
        spread=cluster.spread,
    )


def clusters_to_events(
    clusters: list[EventCluster], *, include_indirect: bool = True
) -> list[Event]:
    return [cluster_to_event(c, include_indirect=include_indirect) for c in clusters]
