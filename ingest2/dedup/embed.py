"""Gemini 임베딩 — 중복제거의 의미 비교용. src/cluster/embed 패턴 미러(독립).

`gemini_embedder()`는 texts -> (N, D) 행렬을 주는 콜러블을 만든다. dedup에 주입식으로
넣어, 키가 없거나 끄고 싶으면 embedder=None으로 결정론 폴백이 되게 한다.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768
BATCH = 80


def gemini_embedder(
    client=None,
    model: str = EMBED_MODEL,
    dim: int = EMBED_DIM,
) -> Callable[[list[str]], np.ndarray]:
    from google.genai import types

    from .. import llm

    client = client or llm.gemini_client()

    def embed(texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, dim), dtype=np.float32)
        vecs: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = texts[start : start + BATCH]
            resp = client.models.embed_content(
                model=model,
                contents=batch,
                config=types.EmbedContentConfig(output_dimensionality=dim),
            )
            vecs.extend(e.values for e in resp.embeddings)
        return np.array(vecs, dtype=np.float32)

    return embed
