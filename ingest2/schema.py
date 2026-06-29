"""ingest2 수집 레이어 공통 스키마 — 모든 소스 어댑터가 이 contract를 따른다."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 소스 신뢰도 등급 (낮을수록 신뢰). 4=크롤링·5=텔레그램은 현 빌드 미사용(향후 대비).
TrustTier = Literal[1, 2, 3, 4, 5]  # 1=공시 2=뉴스API 3=RSS 4=크롤링 5=텔레그램

# 이벤트 유형 통제 어휘 (다운스트림 후보 비교용) — 모르면 "other".
EventType = Literal[
    "earnings",             # 실적 발표
    "guidance_up",          # 가이던스 상향
    "guidance_down",        # 가이던스 하향
    "m_and_a",              # 인수합병
    "ipo",                  # 상장 신청/IPO
    "ceo_change",           # CEO 교체
    "cfo_change",           # CFO 교체
    "litigation",           # 소송
    "regulation",           # 규제
    "supply_chain",         # 공급망 이슈
    "ai_capex",             # AI 투자비
    "price_up",             # 주가 급등 (원인 미상 단일 시그널)
    "price_down",           # 주가 급락
    "filing",               # 일반 공시
    "strategic_investment", # 전략적 투자
    "other",
]

FilterStatus = Literal["pending", "passed", "rejected"]


class RawRecord(BaseModel):
    """원본 1건 — 소스가 준 그대로. 감사·되돌리기용 보험."""

    source_id: str                                      # "sec_edgar", "finnhub", "yahoo_rss" ...
    source_native_id: str                               # 소스 자체 ID (재수집 방지 키)
    content_type: Literal["json", "html", "xml", "text"]
    payload: str                                        # 원문 그대로 (JSON 문자열 / HTML / 텍스트)
    url: str | None = None
    fetched_at: datetime


class NewsItem(BaseModel):
    """정규화된 공통 뉴스 1건 — 다운스트림이 소비하는 표준 양식."""

    # --- 식별 ---
    item_id: str                                        # f"{source_id}:{source_native_id}"
    source_id: str
    source_native_id: str
    trust_tier: TrustTier

    # --- 내용 ---
    title: str
    summary: str = ""
    body: str = ""
    url: str
    canonical_url: str | None = None                    # 원문 URL (중복 제거 기준)
    source_name: str = ""                               # 출처명 (Reuters, SEC ...)
    author: str = ""

    # --- 시간 ---
    published_at: datetime | None = None
    collected_at: datetime

    # --- 메타 ---
    language: str = "en"                                # 미국 단독이나 한국발 예외 대비 필드 유지
    raw_category: str = ""                              # 소스가 준 카테고리 (원본)

    # --- 다운스트림이 채움 (수집 시 best-effort) ---
    companies: list[str] = Field(default_factory=list)
    tickers_direct: list[str] = Field(default_factory=list)
    tickers_indirect: list[str] = Field(default_factory=list)
    event_type: EventType | None = None
    filter_status: FilterStatus = "pending"
    rejected_reasons: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)      # 통과했지만 주의표시
    source_meta: dict[str, str] = Field(default_factory=dict)  # 예: SEC cik


class EventCluster(BaseModel):
    """같은 사건으로 묶인 NewsItem 그룹 — §6 중복제거 산출물. §7 후보·§9 랭킹의 단위."""

    cluster_id: str
    member_ids: list[str]
    representative_id: str            # 대표(최상 신뢰도→최조기) item_id
    title: str                        # 대표 제목
    summary: str = ""
    tickers_direct: list[str] = Field(default_factory=list)
    tickers_indirect: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    trust_tier_best: int = 5          # 멤버 중 최상 신뢰도(=최소 tier)
    spread: int = 1                   # 묶인 기사 수 (중요도 신호)
    published_start: datetime | None = None
    published_end: datetime | None = None
