from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Optional

from app.deep_research.config import (
    GEMINI_API_KEY, GEMINI_LITE_MODEL, LITE_INPUT_COST, LITE_OUTPUT_COST
)
from app.deep_research.models import ResearchPlan, SubQuery, CoverageInfo

logger = logging.getLogger(__name__)


PLAN_PROMPT = """당신은 금융 리서치 전문가입니다. 사용자의 질의를 분석하여 심층 리서치 계획을 세우세요.

[사용자 질의]
{query}

[추가 컨텍스트]
{context}

다음 JSON 형식으로 리서치 계획을 작성하세요:
{{
  "language": "ko 또는 en 또는 both (질의 언어 기반)",
  "sub_queries": [
    {{
      "query": "구체적인 검색 쿼리 (영어 권장, 검색 엔진 친화적)",
      "priority": 1,
      "sources": ["parallel", "tavily", "sec", "dart", "fred", "arxiv"],
      "rationale": "이 쿼리가 필요한 이유",
      "jurisdiction": "이 하위 질문이 다루는 사건의 발생 지역/규제 관할 (예: US, China, EU, Korea, Global)",
      "primary_sources_needed": ["이 쿼리에 가장 권위 있는 1차 출처 유형 (예: SEC 8-K, SZSE 공시, CSRC 성명, FSC 공시, EU 관보 등)"],
      "coverage_note": "이 쿼리에서 확인 가능한 출처와 확인 불가능한 출처 간략 설명"
    }}
  ],
  "required_sections": ["보고서에 포함될 섹션 제목들"],
  "search_strategy": "전반적인 검색 전략 설명",
  "coverage_gaps": ["접근 불가능하거나 언어/지역 장벽으로 커버리지가 제한된 출처 목록 (예: 중국 SZSE 공시, 한국 DART 원문 등)"]
}}

규칙:
1. sub_queries는 최소 8개, 최대 15개
2. 각 쿼리는 서로 다른 각도에서 접근 (현황, 역사, 재무, 시장반응, 규제, 경쟁사, 리스크 등)
3. 영어 쿼리와 한국어 쿼리 혼합 (영어 70%, 한국어 30%)
4. sources 배열에는 해당 쿼리에 적합한 소스만 포함
5. SEC/DART는 공시 관련 쿼리에만, FRED는 거시지표 관련에만, arXiv는 학술 연구 관련에만
6. priority: 1=핵심(즉시 검색), 2=중요, 3=보조
7. required_sections: 최종 보고서 구조 (4~8개 섹션)
8. [출처 라우팅 핵심 규칙] jurisdiction은 '종목 국적'이 아니라 '이 하위 질문에서 다루는 사건의 발생 지역/규제 관할'로 결정하라.
   - 미국 상장 기업이라도 중국에서 발생한 사건이면 jurisdiction=China, primary_sources_needed에 SZSE/CSRC 포함
   - 예: "INDI사의 Wuxi 지분 매각" → jurisdiction=China, primary_sources_needed=["SZSE 공시", "CSRC 성명"], SEC 8-K는 미국 측 시각일 뿐
   - 예: "Tesla recall in EU" → jurisdiction=EU, primary_sources_needed=["EC 공시", "독일 KBA"]
   - 미국 증권법/공시라면 SEC, 한국이라면 DART/FSC, 유럽이라면 EU 규정 기관 등
9. [티커 앵커링 — 필수] context에 ticker 또는 회사명이 있으면 모든 sub_query에 해당 식별자를 반드시 포함할 것.
   - context가 {{"ticker": "INDI"}} 이면 → 각 쿼리에 "indie Semiconductor" 또는 "INDI" 포함
   - "semiconductor market" 같은 종목 식별자 없는 제네릭 쿼리는 전혀 무관한 결과(인사관리, SEO 사이트 등)를 유발하므로 절대 금지
   - 올바른 예: "indie Semiconductor INDI Wuxi stake sale 2023" (X)
   - 잘못된 예: "semiconductor market outlook 2025" (X — INDI 식별자 없음)

JSON만 출력, 다른 텍스트 없음."""


class Planner:
    """Gemini Flash로 질의를 분해하고 검색 전략을 수립."""

    def __init__(self):
        self._model = None
        self._tokens_used: int = 0

    def _get_model(self):
        if self._model is None and GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                self._model = genai.GenerativeModel(GEMINI_LITE_MODEL)
                logger.info(f"[planner] Gemini Lite 초기화: {GEMINI_LITE_MODEL}")
            except Exception as e:
                logger.error(f"[planner] Gemini 초기화 실패: {e}")
        return self._model

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def estimated_cost(self) -> float:
        return self._tokens_used * (LITE_INPUT_COST / 1_000_000)

    async def plan(self, query: str, context: Optional[dict] = None) -> ResearchPlan:
        """질의를 분석하여 리서치 계획 생성."""
        model = self._get_model()
        if model is None:
            logger.warning("[planner] Gemini 사용 불가 — 기본 계획 사용")
            return self._fallback_plan(query)

        context_str = json.dumps(context, ensure_ascii=False) if context else "없음"
        prompt = PLAN_PROMPT.format(query=query, context=context_str)

        try:
            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
                request_options={"timeout": 60},
            )
            raw = response.text.strip()
            self._tokens_used += _count_tokens(raw)

            # JSON 추출
            data = _parse_json(raw)
            if not data:
                logger.warning("[planner] JSON 파싱 실패 — 기본 계획 사용")
                return self._fallback_plan(query)

            sub_queries = [
                SubQuery(
                    query=sq.get("query", ""),
                    priority=sq.get("priority", 2),
                    sources=sq.get("sources", ["parallel", "tavily"]),
                    rationale=sq.get("rationale", ""),
                    jurisdiction=sq.get("jurisdiction", ""),
                    primary_sources_needed=sq.get("primary_sources_needed", []),
                    coverage_note=sq.get("coverage_note", ""),
                )
                for sq in data.get("sub_queries", [])
                if sq.get("query")
            ]

            coverage_gaps = data.get("coverage_gaps", [])

            plan = ResearchPlan(
                original_query=query,
                language=data.get("language", "ko"),
                sub_queries=sub_queries,
                required_sections=data.get("required_sections", _default_sections()),
                search_strategy=data.get("search_strategy", ""),
                coverage_gaps=coverage_gaps,
            )
            logger.info(f"[planner] 계획 완료: {len(sub_queries)}개 쿼리, {len(plan.required_sections)}개 섹션")
            return plan

        except Exception as e:
            logger.error(f"[planner] 계획 생성 실패: {e}")
            return self._fallback_plan(query)

    def _fallback_plan(self, query: str) -> ResearchPlan:
        """Gemini 없을 때 기본 계획."""
        return ResearchPlan(
            original_query=query,
            language="ko",
            sub_queries=[
                SubQuery(query=query, priority=1, sources=["parallel", "tavily"]),
                SubQuery(query=f"{query} latest news", priority=1, sources=["parallel", "tavily"]),
                SubQuery(query=f"{query} SEC filing", priority=2, sources=["sec"]),
                SubQuery(query=f"{query} financial analysis", priority=2, sources=["parallel"]),
                SubQuery(query=f"{query} market reaction", priority=2, sources=["parallel", "tavily"]),
            ],
            required_sections=_default_sections(),
            search_strategy="기본 병렬 검색",
        )


def _default_sections() -> list[str]:
    return ["개요", "현황 및 진행상황", "재무적 영향", "시장 반응", "리스크 요인", "전망"]


def _parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    # 마크다운 코드블록 제거
    text = re.sub(r'^```(?:json)?\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON 블록 추출 시도
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _count_tokens(text: str) -> int:
    return len(text) // 4  # 대략적 추정
