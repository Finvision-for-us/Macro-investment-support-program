"""리서치 결과 스키마."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["positive", "negative", "uncertain"]


class CausalNode(BaseModel):
    """단일 주장(claim) + 출처. is_single_source는 자동 계산."""

    claim: str
    source_urls: list[str] = Field(default_factory=list)

    @property
    def is_single_source(self) -> bool:
        return len(self.source_urls) == 1


class ShallowReport(BaseModel):
    """클러스터 1개에 대한 얕은 리서치 결과 (1샷 grounded)."""

    event_id: str
    background: str
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)


class SubQuestionPlan(BaseModel):
    """깊은 리서치의 계획 단계 출력."""

    sub_questions: list[str] = Field(min_length=3, max_length=6)


class EvidenceItem(BaseModel):
    """깊은 리서치 1개 sub-question의 검색 결과."""

    question: str
    answer: str
    sources: list[str] = Field(default_factory=list)


class DeepReport(BaseModel):
    """깊은 리서치 최종 합성 결과."""

    event_id: str
    background: list[CausalNode] = Field(default_factory=list)
    direct_causes: list[CausalNode] = Field(default_factory=list)
    affected_entities: list[CausalNode] = Field(default_factory=list)
    counter_evidence: list[CausalNode] = Field(default_factory=list)
    watch_points: list[CausalNode] = Field(default_factory=list)
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    all_sources: list[str] = Field(default_factory=list)
