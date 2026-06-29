"""M2 데이터 모델: CausalEdge, Story."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["positive", "negative", "uncertain"]
EdgeSource = Literal["pairwise_llm", "deep_research_claim"]
RippleTier = Literal["direct", "adjacent", "macro"]  # 1차/2차/3차
RippleHorizon = Literal["1w", "1m", "1q"]


class RippleEffect(BaseModel):
    """M3.5: 이 스토리가 만들 것으로 예상되는 파급 1건.

    한 스토리에 여러 RippleEffect 가 붙음. UI 에서 tier 별로 묶어 보여줌.
    """

    tier: RippleTier
    target: str  # 티커 (NVDA), 섹터명 (반도체), 또는 거시 변수 (10년물 금리)
    direction: Direction
    horizon: RippleHorizon
    confidence: float = Field(ge=0.0, le=1.0)
    mechanism: str  # 한 문장 한국어 — 왜 / 어떻게 영향


class CausalEdge(BaseModel):
    """이벤트 간 인과 관계 (from → to)."""

    from_event_id: str
    to_event_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    direction: Direction
    mechanism: str
    source_urls: list[str] = Field(default_factory=list)
    inferred_by: EdgeSource


class Story(BaseModel):
    """연결된 이벤트들을 묶은 스토리 (M2 후반 Day 6~8에서 채워짐)."""

    id: str
    event_ids: list[str]
    title: str = ""
    narrative_short: str = ""  # ~300자
    narrative_long: str = ""   # 800~1500자
    direction: Direction = "uncertain"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    affected_tickers: list[str] = Field(default_factory=list)
    aggregated_impact: float = 0.0
    edges: list[CausalEdge] = Field(default_factory=list)
    all_sources: list[str] = Field(default_factory=list)
    # M3.5: 1·2·3차 파급효과 — narratives 단계 이후 채워짐. 폴백/실패 시 빈 list.
    ripple_effects: list[RippleEffect] = Field(default_factory=list)
