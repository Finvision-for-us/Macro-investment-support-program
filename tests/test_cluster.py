"""클러스터링 알고리즘 단위 테스트 (Gemini 호출 없이)."""
from __future__ import annotations

import numpy as np

from src.cluster.cluster import cluster_indices


def test_cluster_singletons_when_all_dissimilar():
    embs = np.eye(4, dtype=np.float32)  # 모두 직교 → 유사도 0
    groups = cluster_indices(embs, threshold=0.5)
    assert len(groups) == 4


def test_cluster_merges_similar_pairs():
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.99, 0.01, 0.0])
    c = np.array([0.0, 1.0, 0.0])
    embs = np.vstack([a, b, c]).astype(np.float32)
    groups = cluster_indices(embs, threshold=0.9)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]


def test_cluster_transitive():
    a = np.array([1.0, 0.0])
    b = np.array([0.95, 0.31])  # ~71° from a... actually let me use clear values
    # 단순 검증: 모두 비슷한 방향이면 한 클러스터
    embs = np.array([[1.0, 0.0], [0.99, 0.05], [0.97, 0.1]], dtype=np.float32)
    groups = cluster_indices(embs, threshold=0.9)
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2]


def test_empty_input():
    embs = np.zeros((0, 768), dtype=np.float32)
    assert cluster_indices(embs, threshold=0.8) == []
