"""코사인 유사도 + Union-Find 기반 단순 군집화."""
from __future__ import annotations

import hashlib

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from src.ingest.schema import Event, RawNews


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def cluster_indices(embeddings: np.ndarray, threshold: float) -> list[list[int]]:
    """임베딩 행렬 → 같은 사건으로 묶인 인덱스 리스트."""
    n = embeddings.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [[0]]

    sim = cosine_similarity(embeddings)
    uf = _UnionFind(n)
    iu = np.triu_indices(n, k=1)
    over = sim[iu] >= threshold
    for i, j in zip(iu[0][over], iu[1][over], strict=False):
        uf.union(int(i), int(j))

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    return sorted(groups.values(), key=lambda g: -len(g))


def _pick_summary(items: list[RawNews]) -> str:
    """클러스터 대표 요약: 가장 긴 description, 없으면 제목 결합."""
    with_desc = [n for n in items if n.description]
    if with_desc:
        return max(with_desc, key=lambda n: len(n.description)).description
    return " | ".join(n.title for n in items[:3])


def _cluster_id(items: list[RawNews]) -> str:
    raw = "|".join(sorted(n.id for n in items))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def build_events(news: list[RawNews], embeddings: np.ndarray, threshold: float) -> list[Event]:
    """뉴스 + 임베딩 → Event 리스트 (spread 내림차순)."""
    groups = cluster_indices(embeddings, threshold)
    events: list[Event] = []
    for group in groups:
        items = [news[i] for i in group]
        events.append(
            Event.from_cluster(
                cluster_id=_cluster_id(items),
                items=items,
                summary=_pick_summary(items),
            )
        )
    events.sort(key=lambda e: -e.spread)
    return events
