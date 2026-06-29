"""Gemini 임베딩 생성기 + 디스크 캐시."""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt
from tenacity.wait import wait_base

from src.config import EMBEDDING_DIM, EMBEDDING_MODEL, GEMINI_API_KEY
from src.ingest.schema import RawNews

EMBED_BATCH_SIZE = 80  # gemini-embedding-001: 1 batch = 1 quota unit
INTER_BATCH_SLEEP_SEC = 1.0


class _WaitFromQuotaError(wait_base):
    """Gemini 429에서 retryDelay를 파싱해 그만큼 대기. 그 외는 지수 백오프."""

    def __call__(self, retry_state) -> float:  # noqa: D401
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is not None:
            m = re.search(r"retry in ([\d.]+)s", str(exc))
            if m:
                return float(m.group(1)) + 2.0
        return min(2 ** retry_state.attempt_number, 30)


class MissingGeminiKeyError(RuntimeError):
    """Gemini API 키 미설정 — retry 대상에서 제외."""


def _client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise MissingGeminiKeyError(
            "GEMINI_API_KEY not set. Get one at https://aistudio.google.com/apikey "
            "and add it to .env"
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _to_text(n: RawNews) -> str:
    """임베딩 입력 텍스트. description이 비면 title만 사용."""
    if n.description:
        return f"{n.title}\n\n{n.description}"
    return n.title


def embed_texts(texts: list[str]) -> np.ndarray:
    """일반 텍스트 리스트 → 임베딩 행렬 (N, D). 배치 처리 + 무료 tier rate 여유."""
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

    client = _client()
    vectors: list[list[float]] = []

    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        vectors.extend(_embed_batch(client, batch))
        if start + EMBED_BATCH_SIZE < len(texts):
            time.sleep(INTER_BATCH_SLEEP_SEC)

    return np.array(vectors, dtype=np.float32)


def embed_news(items: list[RawNews]) -> np.ndarray:
    """뉴스 리스트를 임베딩 행렬 (N, D)로 변환."""
    return embed_texts([_to_text(n) for n in items])


@retry(stop=stop_after_attempt(5), wait=_WaitFromQuotaError(), reraise=True)
def _embed_batch(client: genai.Client, texts: list[str]) -> list[list[float]]:
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
    )
    return [e.values for e in response.embeddings]


def _cache_path(raw_path: Path) -> Path:
    """raw JSON 옆에 .emb.npz로 임베딩 캐시 저장."""
    return raw_path.with_suffix(".emb.npz")


def load_or_compute_embeddings(raw_path: Path, news: list[RawNews]) -> tuple[np.ndarray, bool]:
    """캐시가 유효하면 로드, 아니면 새로 계산 + 저장.

    Returns:
        (embeddings, cache_hit)
    """
    cache = _cache_path(raw_path)
    current_ids = np.array([n.id for n in news])

    if cache.exists():
        data = np.load(cache, allow_pickle=False)
        cached_ids = data["news_ids"]
        cached_model = str(data["model"])
        cached_dim = int(data["dim"])
        if (
            cached_model == EMBEDDING_MODEL
            and cached_dim == EMBEDDING_DIM
            and cached_ids.shape == current_ids.shape
            and np.array_equal(cached_ids, current_ids)
        ):
            return data["embeddings"].astype(np.float32), True

    embeddings = embed_news(news)
    np.savez(
        cache,
        embeddings=embeddings,
        news_ids=current_ids,
        model=np.array(EMBEDDING_MODEL),
        dim=np.array(EMBEDDING_DIM),
    )
    return embeddings, False
