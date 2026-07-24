from __future__ import annotations
import asyncio
import logging
from typing import Optional

from app.deep_research.config import (
    GEMINI_API_KEY, DEEP_RESEARCH_CRITIC_MODEL, DEEP_RESEARCH_VERIFY_MODEL,
    ENABLE_CRITIC_GROUNDING,
)
from pydantic import BaseModel

from app.deep_research.models import (
    ExtractedContent, GapAnalysis, ResearchPlan, SubQuery
)
from app.deep_research.agents import numeric_consistency
from app.deep_research import llm_client
from app.deep_research.common import parse_json_object

logger = logging.getLogger(__name__)


# ── 구조화 출력 스키마 (CRITIC_PROMPT의 JSON 형식과 1:1) ──
class AdditionalQueryOut(BaseModel):
    query: str = ""
    priority: int = 2
    sources: list[str] = ["parallel", "tavily"]
    rationale: str = ""


class GapOut(BaseModel):
    is_sufficient: bool = False
    confidence: float = 0.5
    gaps: list[str] = []
    additional_queries: list[AdditionalQueryOut] = []
    reasoning: str = ""

# ── 신형 SDK (google-genai) — critic.py 내부에서만 사용, 레거시와 격리 ──
try:
    from google import genai as _genai_new
    from google.genai import types as _genai_types
    _GENAI_NEW_AVAILABLE = True
except ImportError:
    _GENAI_NEW_AVAILABLE = False
    logger.warning("[critic] google-genai 미설치 — grounding 비활성화")


CRITIC_PROMPT = """당신은 최고 수준의 금융 리서치 품질 검토자입니다.

[원본 질의]
{query}

[지금까지 수집된 정보 요약]
{content_summary}

[필요한 보고서 섹션]
{required_sections}

[현재 리서치 이터레이션]
{iteration}회차

다음 6개 항목을 평가하세요:
1. 수집된 정보가 질의에 충분히 답하는가?
2. 어떤 중요한 정보가 빠져있는가?
3. 추가로 검색해야 할 구체적 쿼리는?
4. [모순 점검] 수집된 정보 간 상충하거나 모순되는 내용이 있는가?
   있으면 additional_queries에 확인 쿼리를 추가하라.
5. [인과 근거 점검] 보고서에서 핵심 인과 주장(예: 정책→실적, 사건→주가 반응)의
   실제 근거가 수집됐는가, 아니면 추측 수준인가?
   근거가 부족한 인과 주장이 있으면 gaps에 명시하고 additional_queries를 생성하라.
6. [관점 균형 점검] 강세론(bull case)과 약세론(bear case) 양쪽 근거가 모두 있는가?
   한쪽만 있으면 반대 관점 보완 쿼리를 additional_queries에 추가하라.

JSON 형식으로 응답:
{{
  "is_sufficient": true 또는 false,
  "confidence": 0.0~1.0 (현재 정보의 충분성),
  "gaps": ["빠진 정보 1", "빠진 정보 2", ...],
  "additional_queries": [
    {{
      "query": "추가 검색 쿼리",
      "priority": 1,
      "sources": ["parallel", "tavily"],
      "rationale": "이 쿼리가 필요한 이유"
    }}
  ],
  "reasoning": "전체 평가 요약 (모순/인과/관점 균형 상태 포함)"
}}

규칙:
- is_sufficient=true: 핵심 질문에 80% 이상 답할 수 있을 때
- 1회차(iteration=1)에서는 모순·인과·관점 균형 중 하나라도 미흡하면 is_sufficient=false
- additional_queries: 최대 5개, 정말 필요한 것만
- 이미 찾은 정보와 중복되는 쿼리 제외
JSON만 출력."""


# ── grounding 전용 프롬프트 (신형 SDK, google_search tool 사용 시) ──
GROUNDING_PROMPT = """당신은 최신 금융 뉴스를 실시간으로 확인하는 리서치 보조입니다.
Google Search로 현재 시점의 최신 정보를 확인하세요.

[리서치 주제]
{query}

[현재까지 수집된 정보 요약 — 이미 알고 있는 내용]
{content_summary}

위 수집 정보 이후, 지금 이 시점 기준으로 이 주제와 관련하여 발생한
더 최신의 중요한 사건·발표·수치 변화·규제 이슈가 있는지 Google Search로 확인하세요.

있다면: 어떤 내용을 추가 검색해야 하는지 구체적인 검색 쿼리를 최대 3개 만들어주세요.
없다면: recent_gaps를 빈 배열로 반환하세요.

규칙:
- 검색으로 찾은 실제 사실·수치·인용문은 응답에 포함하지 마세요.
- 어떤 추가 검색이 필요한지 쿼리 문자열만 반환하세요.
- 이미 수집된 정보와 중복되는 쿼리는 제외하세요.

JSON만 출력:
{{"recent_gaps": ["검색 쿼리 1", "검색 쿼리 2"]}}"""


class Critic:
    """수집된 정보의 충분성을 평가하고 추가 쿼리를 생성."""

    def __init__(self):
        self._model = None
        self._tokens_used: int = 0

    def reset_usage(self) -> None:
        """잡 시작 시 토큰 카운터 초기화 — 비용 집계가 잡 간 누적되지 않게."""
        self._tokens_used = 0

    def _get_model(self):
        if self._model is None and GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                self._model = genai.GenerativeModel(DEEP_RESEARCH_CRITIC_MODEL)
                logger.info(f"[critic] Gemini 모델 초기화: {DEEP_RESEARCH_CRITIC_MODEL}")
            except Exception as e:
                logger.error(f"[critic] Gemini 초기화 실패: {e}")
        return self._model

    def _get_verify_fallback(self):
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(DEEP_RESEARCH_VERIFY_MODEL)
            logger.warning(f"[critic] critic 모델 불가 → verify 모델 폴백: {DEEP_RESEARCH_VERIFY_MODEL}")
            return model
        except Exception as e:
            logger.error(f"[critic] Lite 폴백도 실패: {e}")
            return None

    async def _grounding_check(
        self,
        query: str,
        content_summary: str,
    ) -> list[SubQuery]:
        """신형 SDK + google_search grounding으로 최신 누락 사건 탐지.

        grounding이 찾은 사실 자체는 반환하지 않는다.
        "어떤 주제를 추가 검색해야 하는지" 쿼리 문자열만 SubQuery로 변환해 반환.
        실패 시 빈 리스트 반환 — 평가 전체는 영향받지 않는다.
        """
        if not _GENAI_NEW_AVAILABLE:
            logger.warning("[critic] google-genai 미설치 → grounding 스킵")
            return []
        if not GEMINI_API_KEY:
            return []

        prompt = GROUNDING_PROMPT.format(
            query=query,
            content_summary=content_summary[:2000],
        )
        try:
            client = _genai_new.Client(api_key=GEMINI_API_KEY)
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=DEEP_RESEARCH_CRITIC_MODEL,
                contents=prompt,
                config=_genai_types.GenerateContentConfig(
                    tools=[_genai_types.Tool(
                        google_search=_genai_types.GoogleSearch()
                    )],
                ),
            )
            text = response.text.strip() if response.text else ""
            data = parse_json_object(text)
            if not data or not isinstance(data.get("recent_gaps"), list):
                return []

            queries: list[SubQuery] = []
            for q_str in data["recent_gaps"][:3]:
                if q_str and isinstance(q_str, str):
                    queries.append(SubQuery(
                        query=q_str,
                        priority=1,
                        sources=["tavily", "parallel"],
                        rationale="Google Search grounding으로 탐지된 최신 누락 정보",
                    ))
            if queries:
                logger.info(f"[critic] grounding 탐지 쿼리 {len(queries)}개")
            return queries
        except Exception as e:
            logger.warning(f"[critic] grounding 호출 실패 (무시): {e}")
            return []

    def _augment_with_numeric(
        self,
        result: GapAnalysis,
        contents: list[ExtractedContent],
        iteration: int,
    ) -> GapAnalysis:
        """결정론적 수치 정합성 검사 결과를 GapAnalysis에 병합.

        - 산술(pro-rata/세율)·프레이밍(gross↔net) 상충은 gaps + 후속 쿼리로.
        - 오직 실제 추출된 contents만 대상 → '열지 못한 값'은 상충 근거가 되지 않는다.
        - 실패해도 원래 result를 그대로 반환(평가 전체에 영향 없음).
        """
        try:
            nres = numeric_consistency.analyze(contents)
        except Exception as e:
            logger.warning(f"[critic] 수치정합 검사 실패(무시): {e}")
            return result
        if not (nres.conflicts or nres.followup_queries):
            if nres.consistent:
                logger.info(f"[critic] 수치정합: 정합 {len(nres.consistent)}건 (상충 없음)")
            return result

        existing_q = {q.query for q in result.additional_queries}
        new_qs = [
            SubQuery(
                query=q, priority=1, sources=["parallel", "tavily"],
                rationale="수치 정합성 검사(결정론적)로 탐지된 재확인 필요 항목",
            )
            for q in nres.followup_queries if q not in existing_q
        ]
        is_sufficient = result.is_sufficient
        # 미해결 수치 상충이 있으면 초기 이터레이션에서 한 번 더 파고든다(무한루프 방지).
        if nres.conflicts and is_sufficient and iteration <= 2:
            is_sufficient = False
            logger.info("[critic] 수치 상충 탐지 → is_sufficient=false (재검색)")

        merged = GapAnalysis(
            is_sufficient=is_sufficient,
            confidence=result.confidence,
            gaps=list(result.gaps) + list(nres.conflicts),
            additional_queries=list(result.additional_queries) + new_qs,
            reasoning=(
                result.reasoning
                + f" [수치정합: 정합 {len(nres.consistent)} / "
                + f"상충·재확인 {len(nres.conflicts)}]"
            ),
        )
        logger.info(
            f"[critic] 수치정합 병합: 정합 {len(nres.consistent)}, "
            f"상충 {len(nres.conflicts)}, 추가쿼리 {len(new_qs)}"
        )
        return merged

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    async def evaluate(
        self,
        plan: ResearchPlan,
        contents: list[ExtractedContent],
        iteration: int = 1,
    ) -> GapAnalysis:
        """수집된 콘텐츠의 충분성 평가."""
        if not contents:
            return self._fallback_analysis(plan, contents)

        content_summary = _summarize_contents(contents, max_chars=16000)
        sections_str = "\n".join(f"- {s}" for s in plan.required_sections)

        prompt = CRITIC_PROMPT.format(
            query=plan.original_query,
            content_summary=content_summary,
            required_sections=sections_str,
            iteration=iteration,
        )

        # ── 1차: 구조화 출력 (quota 시 verify 모델 재시도는 llm_client 내장) ──
        data: Optional[dict] = None
        sres = await llm_client.generate_structured(
            prompt, GapOut, DEEP_RESEARCH_CRITIC_MODEL,
            timeout_s=120, fallback_model=DEEP_RESEARCH_VERIFY_MODEL, tag="critic",
        )
        if sres is not None:
            data = sres.data.model_dump()
            self._tokens_used += sres.output_tokens
            logger.info("[critic] 구조화 출력 사용")

        try:
            if data is None:
                # ── 2차(레거시): 자유텍스트 + 정규식 파싱 — 구조화 실패 시 동작 보존 ──
                model = self._get_model()
                if model is None:
                    return self._fallback_analysis(plan, contents)
                try:
                    response = await asyncio.to_thread(
                        model.generate_content,
                        prompt,
                        request_options={"timeout": 120},
                    )
                except Exception as pro_err:
                    if "quota" in str(pro_err).lower() or "429" in str(pro_err):
                        logger.warning("[critic] 모델 할당량 초과 → verify 모델 재시도")
                        verify_model = self._get_verify_fallback()
                        if verify_model is None:
                            return GapAnalysis(is_sufficient=True, confidence=0.5, gaps=[], additional_queries=[], reasoning="critic/verify 모델 모두 불가")
                        response = await asyncio.to_thread(
                            verify_model.generate_content,
                            prompt,
                            request_options={"timeout": 120},
                        )
                    else:
                        raise
                raw = response.text.strip()
                self._tokens_used += len(raw) // 4

                data = parse_json_object(raw)
                if not data:
                    logger.warning("[critic] JSON 파싱 실패 — 충분하다고 가정")
                    return GapAnalysis(
                        is_sufficient=True, confidence=0.6,
                        gaps=[], additional_queries=[], reasoning="평가 실패"
                    )

            additional = [
                SubQuery(
                    query=q.get("query", ""),
                    priority=q.get("priority", 2),
                    sources=q.get("sources", ["parallel", "tavily"]),
                    rationale=q.get("rationale", ""),
                )
                for q in data.get("additional_queries", [])
                if q.get("query")
            ]

            is_sufficient = data.get("is_sufficient", False)
            # LLM이 confidence를 문자열('high' 등)/비정상값으로 줘도 크래시하지 않게 float로 강제.
            # (float가 아니면 아래 비교·GapAnalysis 검증에서 예외 → critic 평가 전체가 폴백되던 문제)
            confidence = _coerce_float_confidence(data.get("confidence", 0.5))

            # 1회차는 추가 검색 최소 1회 강제
            if iteration == 1 and is_sufficient and confidence < 0.85:
                is_sufficient = False
                logger.info("[critic] 1회차 강제 보완: is_sufficient → false")

            result = GapAnalysis(
                is_sufficient=is_sufficient,
                confidence=confidence,
                gaps=data.get("gaps", []),
                additional_queries=additional,
                reasoning=data.get("reasoning", ""),
            )

            # ── grounding 보조 단계 (ENABLE_CRITIC_GROUNDING=true 시) ──
            # grounding은 "보완 쿼리 생성"의 단서로만 사용.
            # grounding이 찾은 사실 자체는 raw_sources나 보고서에 주입하지 않는다.
            if ENABLE_CRITIC_GROUNDING:
                grounding_queries = await self._grounding_check(
                    plan.original_query, content_summary
                )
                if grounding_queries:
                    existing_qs = {q.query for q in result.additional_queries}
                    new_qs = [q for q in grounding_queries if q.query not in existing_qs]
                    if new_qs:
                        merged = list(result.additional_queries) + new_qs
                        result = GapAnalysis(
                            is_sufficient=result.is_sufficient,
                            confidence=result.confidence,
                            gaps=result.gaps,
                            additional_queries=merged,
                            reasoning=(
                                result.reasoning
                                + f" [grounding: 최신 쿼리 {len(new_qs)}개 추가]"
                            ),
                        )

            # ── 결정론적 수치 정합성 검사 병합 (LLM 산술 오류와 무관) ──
            result = self._augment_with_numeric(result, contents, iteration)

            logger.info(
                f"[critic] 이터레이션 {iteration}: "
                f"충분={result.is_sufficient}, 신뢰도={result.confidence:.2f}, "
                f"갭={len(result.gaps)}개"
                + (f", grounding ON" if ENABLE_CRITIC_GROUNDING else "")
            )
            return result

        except Exception as e:
            logger.error(f"[critic] 평가 실패: {e}")
            return self._fallback_analysis(plan, contents)

    def _fallback_analysis(self, plan: ResearchPlan, contents: list[ExtractedContent]) -> GapAnalysis:
        is_sufficient = len(contents) >= 5
        base = GapAnalysis(
            is_sufficient=is_sufficient,
            confidence=0.5 if is_sufficient else 0.3,
            gaps=[] if is_sufficient else ["더 많은 출처 필요"],
            additional_queries=[],
            reasoning="자동 평가 (Gemini 미사용)",
        )
        # LLM 없이도 결정론적 수치검사는 유효 (iteration=99 → is_sufficient 강제전환 안 함)
        return self._augment_with_numeric(base, contents, iteration=99)


_CONF_WORD = {"high": 0.9, "높음": 0.9, "medium": 0.6, "med": 0.6, "보통": 0.6,
              "중간": 0.6, "low": 0.3, "낮음": 0.3, "none": 0.5, "": 0.5}


def _coerce_float_confidence(value, default: float = 0.5) -> float:
    """LLM confidence를 [0,1] float로 안전 변환. 'high'류 문자열/비정상값도 처리."""
    if isinstance(value, (int, float)):
        try:
            return max(0.0, min(1.0, float(value)))
        except (ValueError, TypeError):
            return default
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _CONF_WORD:
            return _CONF_WORD[s]
        try:
            return max(0.0, min(1.0, float(s)))
        except ValueError:
            return default
    return default


def _summarize_contents(contents: list[ExtractedContent], max_chars: int = 16000) -> str:
    lines = []
    remaining = max_chars
    for i, c in enumerate(contents, 1):
        snippet = c.content[:800].replace("\n", " ")
        line = f"[{i}] {c.title} ({c.domain})\n{snippet}\n"
        if remaining - len(line) < 0:
            break
        lines.append(line)
        remaining -= len(line)
    return "\n".join(lines)
