from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum
import uuid
from datetime import datetime, timezone


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CredibilityLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# ── 검색 결과 ──

class SearchResult(BaseModel):
    url: str
    title: str
    content: str  # snippet or excerpt
    source_type: str  # parallel / tavily / sec / dart / fred / arxiv
    relevance_score: float = 0.0
    published_date: Optional[str] = None
    publisher: Optional[str] = None
    document_type: Optional[str] = None
    reporting_period: Optional[str] = None
    source_section: Optional[str] = None


# ── 추출된 전문 ──

class ExtractedContent(BaseModel):
    url: str
    title: str
    content: str
    domain: str
    word_count: int = 0
    extracted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    document_type: Optional[str] = None
    reporting_period: Optional[str] = None
    source_section: Optional[str] = None
    source_type: Optional[str] = None


# ── 최종 보고서 구성요소 ──

class SourceInfo(BaseModel):
    url: str
    title: str
    domain: str
    credibility: CredibilityLevel = CredibilityLevel.MEDIUM
    accessed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ref_number: Optional[int] = None  # 본문 inline [n] 각주 번호
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    document_type: Optional[str] = None
    reporting_period: Optional[str] = None
    source_section: Optional[str] = None
    source_type: Optional[str] = None


class KeyFinding(BaseModel):
    finding: str
    confidence: ConfidenceLevel
    sources: list[str]


class ClaimRecord(BaseModel):
    """최종 보고서에 실제 포함된 핵심 주장과 검증 결과의 연결 원장."""
    claim_id: str
    research_run_id: str
    claim_text: str
    claim_type: str
    confidence: ConfidenceLevel
    verification_status: str
    source_ids: list[str] = Field(default_factory=list)
    evidence_excerpt: Optional[str] = None
    counter_evidence: list[str] = Field(default_factory=list)
    executive_summary_eligible: bool = False


class MetricValue(BaseModel):
    """산업과 무관한 금융·운영 지표 의미 단위."""
    metric_name: str
    value: str
    unit: str
    entity: str = ""
    scope: str = ""
    period: str = ""
    period_type: str = ""
    as_of: Optional[str] = None
    basis: str = ""
    currency: Optional[str] = None
    source_id: Optional[str] = None


class CalculationRecord(BaseModel):
    """보고서에 사용된 파생 계산과 입력 의미를 보존하는 범용 원장."""
    calculation_id: str
    research_run_id: str
    calculation_type: str
    description: str
    formula: str
    inputs: list[MetricValue] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    required_alignment: list[str] = Field(default_factory=list)
    output: Optional[MetricValue] = None
    validation_status: str = "needs_review"
    validation_errors: list[str] = Field(default_factory=list)
    recomputed_value: Optional[str] = None
    recomputation_delta: Optional[str] = None
    executive_summary_eligible: bool = False


class ScenarioCase(BaseModel):
    name: str
    probability: float
    assumptions: list[str] = Field(default_factory=list)
    outputs: list[MetricValue] = Field(default_factory=list)
    invalidation_triggers: list[str] = Field(default_factory=list)
    evidence_source_ids: list[str] = Field(default_factory=list)


class ScenarioAnalysis(BaseModel):
    research_run_id: str
    cases: list[ScenarioCase] = Field(default_factory=list)
    validation_status: str = "needs_review"
    validation_errors: list[str] = Field(default_factory=list)
    executive_summary_eligible: bool = False


class TimelineEvent(BaseModel):
    date: str
    event: str
    source: str


class ReportSection(BaseModel):
    title: str
    content: str
    sources: list[str]


class ResearchMetadata(BaseModel):
    total_queries: int = 0
    total_sources: int = 0
    iterations: int = 0
    elapsed_seconds: float = 0.0
    gemini_tokens_used: int = 0
    estimated_cost_usd: float = 0.0
    discovery_leads: int = 0       # n차 단서추적으로 탐색한 단서 수
    recovered_sources: int = 0     # 접근가능본 복구로 확보한 출처 수
    generated_queries: list[str] = Field(default_factory=list)       # 실제 생성/실행 대상 검색 쿼리
    official_source_queries: list[str] = Field(default_factory=list) # 실제 공식 site: 검색 쿼리
    searched_official_domains: list[str] = Field(default_factory=list)
    counter_evidence_queries: list[str] = Field(default_factory=list)


class SearchAttempt(BaseModel):
    """검색 시도 1건의 run-scoped 관측 기록."""
    query: str
    source: str
    status: str
    result_count: int = 0
    duration_ms: int = 0
    error_type: Optional[str] = None
    message: str = ""


class CoverageInfo(BaseModel):
    checked: list[str] = Field(default_factory=list)    # 실제로 확인한 출처/관할
    unchecked: list[str] = Field(default_factory=list)  # 확인 못 한 출처/관할 + 이유
    notes: str = ""


# ── 연구 계획 (Planner 출력) ──

class SubQuery(BaseModel):
    query: str
    priority: int = 1  # 1=높음, 3=낮음
    sources: list[str] = Field(default_factory=list)  # 우선 검색할 소스
    rationale: str = ""
    jurisdiction: str = ""               # 사건 발생 지역/규제 관할
    primary_sources_needed: list[str] = Field(default_factory=list)
    coverage_note: str = ""


class ResearchPlan(BaseModel):
    original_query: str
    language: str = "ko"  # ko / en / both
    sub_queries: list[SubQuery]
    required_sections: list[str]
    search_strategy: str = ""
    coverage_gaps: list[str] = Field(default_factory=list)


# ── 비평 결과 (Critic 출력) ──

class GapAnalysis(BaseModel):
    is_sufficient: bool
    confidence: float  # 0~1
    gaps: list[str]
    additional_queries: list[SubQuery]
    reasoning: str


# ── API 요청/응답 ──

class DeepResearchRequest(BaseModel):
    query: str
    context: Optional[dict[str, Any]] = None
    max_iterations: Optional[int] = None
    max_sources: Optional[int] = None


class DeepResearchResponse(BaseModel):
    job_id: str
    query: str
    summary: str
    # 자유형 summary와 별개로 검증·승격 조건을 통과한 claim만 조립한 안전 요약.
    safe_executive_summary: str = ""
    sections: list[ReportSection]
    timeline: list[TimelineEvent]
    key_findings: list[KeyFinding]
    sources: list[SourceInfo]
    metadata: ResearchMetadata
    coverage: Optional[CoverageInfo] = None
    # 공식 출처로 검증하지 못한 사실·교차검증 실패·누락 데이터·남은 의문점을 명시.
    # (시중 딥리서치 AI가 하는 '미검증 gap 명시'를 이식 — 무할루시네이션 원칙과 일치)
    unverified_gaps: list[str] = Field(default_factory=list)
    # 핵심 주장별 다출처 교차검증 결과(일치 출처 수/상충 수치). cross_checker 산출.
    cross_validation: list[str] = Field(default_factory=list)
    # 핵심 주장별 유형·근거·검증상태. 기존 응답과 호환되는 additive field.
    claim_ledger: list[ClaimRecord] = Field(default_factory=list)
    calculation_ledger: list[CalculationRecord] = Field(default_factory=list)
    search_attempts: list[SearchAttempt] = Field(default_factory=list)
    scenario_analysis: Optional[ScenarioAnalysis] = None
    # 실제 실행된 검색 쿼리. observer/회귀평가가 응답 JSON만으로 검색 행동을 재현 가능하게 한다.
    generated_queries: list[str] = Field(default_factory=list)
    official_source_queries: list[str] = Field(default_factory=list)
    status: JobStatus = JobStatus.DONE
    error: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress_pct: int = 0
    current_stage: str = ""
    message: str = ""
    result: Optional[DeepResearchResponse] = None
    error: Optional[str] = None


# ── SSE 이벤트 ──

class ProgressEvent(BaseModel):
    job_id: str
    stage: str
    message: str
    progress_pct: int
    data: Optional[dict[str, Any]] = None
