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
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Literal
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

THEME_SIMILARITY_THRESHOLD = 0.73  # 응집도 높은 클러스터를 직접 테마로 사용
MIN_THEME_SIZE = 1

# tier 산정 파라미터
MAX_MAJOR_THEMES = 6      # headline 다음으로 major 로 승격할 최대 테마 수
_BREADTH_BONUS = 0.06     # 스토리 1건 추가당 가중 (여러 스토리로 뭉친 '진짜 테마' 소폭 우대)
_BREADTH_CAP = 3          # breadth 보너스 상한 (노이즈 다수결 방지)

# UI 위계: headline(히어로 1개) / major(주요 테마 그리드) / minor(기타 단신, 접힘)
ThemeTier = Literal["headline", "major", "minor"]


# ---------------------------------------------------------------------------
# 정기 공시(노이즈) 판별
# ---------------------------------------------------------------------------

# SEC 정기/행정 공시 폼 토큰 — 무명 초소형주의 10-Q/S-1/A 제출 알림이 헤드라인을
# 차지하는 문제를 막기 위해 테마 후보에서 제외한다. 실제 M&A·실적 분석 등 뉴스형
# 스토리는 이 정형 패턴(폼 토큰 + 공시 동사)에 걸리지 않는다.
_FILING_FORM_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:10-[KQ]|8-K|6-K|20-F|40-F|11-K|S-1|S-3|S-4|S-8|F-1|F-3|"
    r"424B\d?|DEFA?\s?14A|SC\s?13[DGE]|13F|POS\s?AM|N-CSR)"
    r"(?:/A)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_FILING_VERBS = ("제출", "신고서", "등록 서류", "보고서")


def is_routine_filing(story: Story) -> bool:
    """SEC 정기/행정 공시 알림형 스토리인지 판별.

    제목이 '<회사>, <FORM> 제출/보고서' 형태(폼 토큰 + 공시 동사)면 정기 공시로 본다.
    이런 스토리는 헤드라인/테마 후보에서 제외한다.
    """
    t = story.title or ""
    return bool(_FILING_FORM_RE.search(t)) and any(v in t for v in _FILING_VERBS)


class Theme(BaseModel):
    """묶인 스토리들이 공유하는 거시 테마 — UI 상단 ThemeStrip 단위."""

    id: str
    name: str
    description: str
    story_ids: list[str]
    aggregate_score: float
    affected_tickers: list[str] = Field(default_factory=list)
    direction: Direction = "uncertain"
    tier: ThemeTier = "major"  # 병합 후 부여되는 표시 위계


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



def _theme_weight(stories: list[Story]) -> float:
    """tier 산정용 가중치 = 최고 임팩트 + breadth 보너스.

    '합'이 아니라 '최고 임팩트'를 기준으로 삼아, 단독 고임팩트 스토리(예: 대형 M&A)가
    무명주 정기공시 다수 뭉치보다 위로 올라오게 한다. 여러 스토리로 뭉친 진짜 테마는
    breadth 보너스로 소폭 우대하되, 상한을 둬 개수만으로 헤드라인을 먹지 못하게 한다.
    """
    peak = max((s.aggregated_impact for s in stories), default=0.0)
    breadth = _BREADTH_BONUS * min(len(stories) - 1, _BREADTH_CAP)
    return peak + breadth


def build_themes(
    stories: list[Story],
    *,
    sim_threshold: float = THEME_SIMILARITY_THRESHOLD,
    min_size: int = MIN_THEME_SIZE,
    embed_fn: Callable[[list[str]], np.ndarray] = embed_texts,
    name_fn: Callable[[list[Story]], tuple[str, str]] = name_theme,
    max_major: int = MAX_MAJOR_THEMES,
) -> list[Theme]:
    """클러스터링 → 명명 → tier 부여 → Theme list.

    정기 공시(:func:`is_routine_filing`)는 테마 후보에서 제외한다. 다중 스토리 클러스터는
    LLM 명명(name_fn), 단독 클러스터는 스토리 제목/내러티브를 그대로 사용한다.
    tier 는 :func:`_theme_weight`(최고 임팩트 기준) 순위로 부여 — 단독 스토리도 임팩트가
    크면 headline/major 로 승격된다. ``name_fn`` 주입으로 LLM 회피 가능 (테스트 용도).
    """
    # 정기 공시 노이즈 제거 후 클러스터링
    candidates = [s for s in stories if not is_routine_filing(s)]
    groups = cluster_themes(
        candidates, sim_threshold=sim_threshold, min_size=min_size, embed_fn=embed_fn
    )
    if not groups:
        return []

    multi_groups = [g for g in groups if len(g) > 1]
    single_groups = [g for g in groups if len(g) == 1]

    entries: list[dict] = []
    if multi_groups:
        with ThreadPoolExecutor(max_workers=8) as pool:
            names = list(pool.map(name_fn, multi_groups))
        for g, (nm, ds) in zip(multi_groups, names):
            entries.append({"stories": g, "name": nm, "desc": ds})

    for g in single_groups:
        s = g[0]
        # 단독 테마는 스토리 제목을 이름으로, 짧은 내러티브를 설명으로 재사용(빈 설명 방지).
        entries.append({
            "stories": g,
            "name": (s.title or "단독 시그널")[:80],
            "desc": (s.narrative_short or "")[:300],
        })

    # 집계 + tier 가중치
    built: list[dict] = []
    for e in entries:
        score, tickers, direction = _aggregate(e["stories"])
        built.append({
            **e,
            "score": score,
            "weight": _theme_weight(e["stories"]),
            "tickers": tickers,
            "direction": direction,
        })

    # tier: weight 내림차순 — 1위 headline, 다음 max_major 개 major, 나머지 minor
    ranked = sorted(range(len(built)), key=lambda i: -built[i]["weight"])
    tier_by_i: dict[int, ThemeTier] = {}
    for rank, i in enumerate(ranked):
        if rank == 0:
            tier_by_i[i] = "headline"
        elif rank <= max_major:
            tier_by_i[i] = "major"
        else:
            tier_by_i[i] = "minor"

    themes: list[tuple[Theme, float]] = []
    for i, b in enumerate(built):
        th = Theme(
            id=uuid4().hex[:12],
            name=b["name"],
            description=b["desc"],
            story_ids=[s.id for s in b["stories"]],
            aggregate_score=round(b["score"], 4),
            affected_tickers=b["tickers"],
            direction=b["direction"],
            tier=tier_by_i[i],
        )
        themes.append((th, b["weight"]))

    # headline → major(weight순) → minor(weight순)
    order = {"headline": 0, "major": 1, "minor": 2}
    themes.sort(key=lambda tw: (order[tw[0].tier], -tw[1]))
    return [t for t, _ in themes]
