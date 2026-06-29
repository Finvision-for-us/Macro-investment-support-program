"""M4 Day 3~4: 어제 스냅샷 ↔ 오늘 스토리 매칭 (PROJECT_SPEC §12.2).

각 오늘 스토리에 대해:

1. 어제 스냅샷에서 공통 ticker ≥ :data:`MIN_TICKER_OVERLAP` 인 후보만 추림 (싸게 1차 필터)
2. 제목 + ``narrative_short`` 임베딩 코사인 유사도 계산
3. 임계값 :data:`LINK_SIMILARITY_THRESHOLD` 이상이면서 가장 높은 후보를 parent로 채택
4. ``parent_story_id`` / ``similarity`` / ``linked_at`` / ``first_seen_date`` (부모에서 상속) 세팅

상태 라벨 (active/evolving/resolved)은 다음 단계 ``state.py`` 가 부여.
링크 자체는 결정론적이라 별도 LLM 호출 없음 — 비용 0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from src.cluster.embed import embed_texts
from src.lifecycle.store import LifecycleStory, Snapshot

LINK_SIMILARITY_THRESHOLD = 0.75
MIN_TICKER_OVERLAP = 1

EmbedFn = Callable[[list[str]], np.ndarray]


def _story_text(s: LifecycleStory) -> str:
    """임베딩 입력 텍스트. ``narrative_short`` 있으면 합쳐서 신호 강화.

    둘 다 비어 있으면 빈 문자열 — 호출자가 임베딩에서 제외해야 한다
    (Gemini 가 빈 content 거부).
    """
    parts = [p for p in (s.title, s.narrative_short) if p]
    return "\n\n".join(parts)


def _ticker_overlap(a: list[str], b: list[str]) -> int:
    return len(set(a) & set(b))


def link_to_previous(
    today_stories: list[LifecycleStory],
    previous: Snapshot | None,
    *,
    sim_threshold: float = LINK_SIMILARITY_THRESHOLD,
    min_ticker_overlap: int = MIN_TICKER_OVERLAP,
    embed_fn: EmbedFn = embed_texts,
) -> list[LifecycleStory]:
    """오늘 stories에 어제 parent를 매칭해 **새 list** 를 반환 (원본 변경 X).

    이 함수는 결정론적이며 LLM 호출 없음. 임베딩은 ``embed_fn`` 으로 1회 호출
    (오늘 N개 + 어제 M개의 텍스트 1배치). 테스트에서는 ``embed_fn`` 을 주입해
    Gemini 의존성 없이 검증 가능.
    """
    if not today_stories:
        return []

    # 어제가 없으면 모두 active 그대로 (복사본 반환).
    if previous is None or not previous.stories:
        return [s.model_copy() for s in today_stories]

    yesterday = previous.stories

    # 1) ticker 사전 필터 — 후보 0개면 임베딩 skip
    #    + 빈 텍스트 (title/narrative 둘 다 비어있음) 인 스토리는 임베딩 거부 회피 위해 후보 제외
    candidates_per_today: dict[int, list[int]] = {}
    for ti, t in enumerate(today_stories):
        if not _story_text(t):
            continue
        cands = [
            yi
            for yi, y in enumerate(yesterday)
            if _story_text(y) and _ticker_overlap(t.tickers, y.tickers) >= min_ticker_overlap
        ]
        if cands:
            candidates_per_today[ti] = cands

    if not candidates_per_today:
        return [s.model_copy() for s in today_stories]

    # 2) 임베딩 — 필요한 항목만 1배치
    today_idxs = sorted(candidates_per_today.keys())
    yest_idxs = sorted({yi for cs in candidates_per_today.values() for yi in cs})

    today_emb = embed_fn([_story_text(today_stories[i]) for i in today_idxs])
    yest_emb = embed_fn([_story_text(yesterday[i]) for i in yest_idxs])

    today_row = {ti: row for row, ti in enumerate(today_idxs)}
    yest_col = {yi: col for col, yi in enumerate(yest_idxs)}

    sim = cosine_similarity(today_emb, yest_emb)
    linked_at = datetime.now(timezone.utc).isoformat()

    # 3) 각 today 스토리에 best parent 결정
    result: list[LifecycleStory] = []
    for ti, t in enumerate(today_stories):
        copy = t.model_copy()
        if ti in candidates_per_today:
            row = today_row[ti]
            best_yi: int | None = None
            best_sim: float = -1.0
            for yi in candidates_per_today[ti]:
                s = float(sim[row, yest_col[yi]])
                if s > best_sim:
                    best_sim = s
                    best_yi = yi
            if best_yi is not None and best_sim >= sim_threshold:
                parent = yesterday[best_yi]
                copy.parent_story_id = parent.story_id
                copy.similarity = round(best_sim, 4)
                copy.linked_at = linked_at
                # first_seen_date 는 부모에서 상속 (이 스토리가 처음 본 날 보존)
                copy.first_seen_date = parent.first_seen_date
        result.append(copy)
    return result
