"""M3.5 Day 1~2: Story 단위 테마 클러스터링 (PROJECT_SPEC §11.5).

매 batch 의 narrative 직후 호출 — Top N 스토리들을 임베딩하고
유사도 ≥ :data:`THEME_SIMILARITY_THRESHOLD` 인 그룹을 Union-Find 로 묶어
"AI 인프라 자본지출 가속" 같은 거시 테마를 추출한다. 각 테마는 LLM 1회
호출로 짧은 한국어 이름/설명 생성.

설계 원칙:

- 클러스터링은 **결정론적** — 임베딩 + Union-Find. 테스트 가능.
- 명명만 LLM — 클러스터당 1회 호출 (Top 10 스토리면 보통 3~5개 테마).
- 빈 텍스트 스토리 (narratives top N 외) 는 클러스터 후보에서 제외.
- 1개짜리 클러스터도 유지 (단독 스토리 자체가 의미 있는 테마인 경우).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable
from uuid import uuid4

import numpy as np
from google.genai import types
from pydantic import BaseModel, Field
from sklearn.metrics.pairwise import cosine_similarity

from src.causal.schema import Direction, Story
from src.cluster.embed import embed_texts
from src.config import GEMINI_MODEL_FAST
from src.cost_guard import log_call
from src.llm import gemini_client, retry_gemini

THEME_SIMILARITY_THRESHOLD = 0.70  # narrative 추상도 감안 — clustering 의 0.82 보다 낮음
MIN_THEME_SIZE = 1


class Theme(BaseModel):
    """묶인 스토리들이 공유하는 거시 테마 — UI 상단 ThemeStrip 단위."""

    id: str
    name: str
    description: str
    story_ids: list[str]
    aggregate_score: float
    affected_tickers: list[str] = Field(default_factory=list)
    direction: Direction = "uncertain"


# ---------------------------------------------------------------------------
# 클러스터링 (결정론적)
# ---------------------------------------------------------------------------


def _story_text(s: Story) -> str:
    parts = [p for p in (s.title, s.narrative_short) if p]
    return "\n\n".join(parts)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_themes(
    stories: list[Story],
    *,
    sim_threshold: float = THEME_SIMILARITY_THRESHOLD,
    min_size: int = MIN_THEME_SIZE,
    embed_fn: Callable[[list[str]], np.ndarray] = embed_texts,
) -> list[list[Story]]:
    """Story 단위 임베딩 + Union-Find → 테마별 list[list]. 점수 합 내림차순.

    LLM 호출 없음 — 결정론적. 테스트에서 ``embed_fn`` 주입 가능.
    """
    indexed = [(i, s) for i, s in enumerate(stories) if _story_text(s)]
    if not indexed:
        return []
    if len(indexed) == 1:
        return [[indexed[0][1]]] if min_size <= 1 else []

    texts = [_story_text(s) for _, s in indexed]
    emb = embed_fn(texts)
    sim = cosine_similarity(emb)

    n = len(indexed)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= sim_threshold:
                uf.union(i, j)

    groups: dict[int, list[Story]] = {}
    for i, (_, s) in enumerate(indexed):
        groups.setdefault(uf.find(i), []).append(s)

    out = [g for g in groups.values() if len(g) >= min_size]
    # 테마 합산 점수 내림차순 — UI 상단에 큰 테마부터
    out.sort(key=lambda g: -sum(s.aggregated_impact for s in g))
    return out


# ---------------------------------------------------------------------------
# 명명 (LLM)
# ---------------------------------------------------------------------------


_NAMING_PROMPT = """You are a financial analyst grouping today's stories into macro themes.

STORIES IN THIS GROUP
{stories_block}

TASK
Produce a single concise macro theme (Korean, 한국어):
- name: 5~20자 한국어. The umbrella that unites these stories.
  예: "AI 인프라 자본지출 가속", "관세/공급망 리스크", "연준 금리 인하 기대"
- description: 한 문장 한국어 (50~120자) explaining what is happening at the macro level.

RULES
- Do NOT invent facts beyond what stories provide.
- name 은 일반명사구 (특정 종목명만 단독 사용 금지 — "엔비디아 실적" X, "AI 반도체 수요" O).
- 한국어 자연스럽게.

Return ONLY JSON in this exact shape:
{{
  "name": "...",
  "description": "..."
}}
"""


def _strip_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    return m.group(1) if m else text


def _format_stories_block(stories: list[Story]) -> str:
    parts = []
    for i, s in enumerate(stories[:8], 1):  # 최대 8개 — 토큰 절감
        tickers = ", ".join(s.affected_tickers[:6]) or "(none)"
        short = (s.narrative_short or "")[:200]
        parts.append(f"[{i}] {s.title}\n    tickers: {tickers}\n    {short}")
    return "\n\n".join(parts)


@retry_gemini
def _call(prompt: str) -> dict:
    client = gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )
    log_call("gemini", "generate", notes="theme naming")
    return json.loads(_strip_json(response.text or "{}"))


def _aggregate(stories: list[Story]) -> tuple[float, list[str], Direction]:
    """클러스터의 합산 점수 / 영향 ticker 유니온 / direction (다수결)."""
    score = sum(s.aggregated_impact for s in stories)
    tickers: list[str] = []
    seen: set[str] = set()
    for s in stories:
        for t in s.affected_tickers:
            if t not in seen:
                seen.add(t)
                tickers.append(t)
    dirs = Counter(s.direction for s in stories)
    direction = dirs.most_common(1)[0][0] if dirs else "uncertain"
    return score, tickers, direction


def name_theme(stories: list[Story]) -> tuple[str, str]:
    """LLM 1회 호출 — 짧은 한국어 테마명 + 설명. 실패 시 폴백."""
    try:
        prompt = _NAMING_PROMPT.format(stories_block=_format_stories_block(stories))
        result = _call(prompt)
        name = str(result.get("name", "")).strip()[:60]
        desc = str(result.get("description", "")).strip()[:300]
        if not name:
            raise ValueError("empty name")
        return name, desc
    except Exception:  # noqa: BLE001
        # 폴백 — 첫 스토리 제목에서 추출
        head = (stories[0].title or "기타 테마")[:30]
        return head, ""


def build_themes(
    stories: list[Story],
    *,
    sim_threshold: float = THEME_SIMILARITY_THRESHOLD,
    min_size: int = MIN_THEME_SIZE,
    embed_fn: Callable[[list[str]], np.ndarray] = embed_texts,
    name_fn: Callable[[list[Story]], tuple[str, str]] = name_theme,
) -> list[Theme]:
    """원샷: 클러스터링 → 명명 → Theme 객체 list. 점수 합 내림차순.

    ``name_fn`` 주입으로 테스트에서 LLM 호출 회피 가능.
    """
    groups = cluster_themes(
        stories, sim_threshold=sim_threshold, min_size=min_size, embed_fn=embed_fn
    )
    themes: list[Theme] = []
    for g in groups:
        name, desc = name_fn(g)
        score, tickers, direction = _aggregate(g)
        themes.append(
            Theme(
                id=uuid4().hex[:12],
                name=name,
                description=desc,
                story_ids=[s.id for s in g],
                aggregate_score=round(score, 4),
                affected_tickers=tickers,
                direction=direction,
            )
        )
    return themes
