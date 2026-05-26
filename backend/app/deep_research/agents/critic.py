from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Optional

from app.deep_research.config import (
    GEMINI_API_KEY, GEMINI_FLASH_MODEL, GEMINI_LITE_MODEL,
    ENABLE_CRITIC_GROUNDING,
)
from app.deep_research.models import (
    ExtractedContent, GapAnalysis, ResearchPlan, SubQuery
)

logger = logging.getLogger(__name__)

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

    def _get_model(self):
        if self._model is None and GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                self._model = genai.GenerativeModel(GEMINI_FLASH_MODEL)
                logger.info(f"[critic] Gemini Flash 초기화: {GEMINI_FLASH_MODEL}")
            except Exception as e:
                logger.error(f"[critic] Gemini 초기화 실패: {e}")
        return self._model

    def _get_flash_fallback(self):
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_LITE_MODEL)
            logger.warning(f"[critic] Flash 불가 → Lite 폴백: {GEMINI_LITE_MODEL}")
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
                model=GEMINI_FLASH_MODEL,
                contents=prompt,
                config=_genai_types.GenerateContentConfig(
                    tools=[_genai_types.Tool(
                        google_search=_genai_types.GoogleSearch()
                    )],
                ),
            )
            text = response.text.strip() if response.text else ""
            data = _parse_json(text)
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
        model = self._get_model()

        if model is None or not contents:
            return self._fallback_analysis(plan, contents)

        content_summary = _summarize_contents(contents, max_chars=16000)
        sections_str = "\n".join(f"- {s}" for s in plan.required_sections)

        prompt = CRITIC_PROMPT.format(
            query=plan.original_query,
            content_summary=content_summary,
            required_sections=sections_str,
            iteration=iteration,
        )

        try:
            try:
                response = await asyncio.to_thread(
                    model.generate_content,
                    prompt,
                    request_options={"timeout": 120},
                )
            except Exception as pro_err:
                if "quota" in str(pro_err).lower() or "429" in str(pro_err):
                    logger.warning(f"[critic] Pro 할당량 초과 → Flash 재시도")
                    flash = self._get_flash_fallback()
                    if flash is None:
                        return GapAnalysis(is_sufficient=True, confidence=0.5, gaps=[], additional_queries=[], reasoning="Pro/Flash 모두 불가")
                    response = await asyncio.to_thread(
                        flash.generate_content,
                        prompt,
                        request_options={"timeout": 120},
                    )
                else:
                    raise
            raw = response.text.strip()
            self._tokens_used += len(raw) // 4

            data = _parse_json(raw)
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
            confidence = data.get("confidence", 0.5)

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
        return GapAnalysis(
            is_sufficient=is_sufficient,
            confidence=0.5 if is_sufficient else 0.3,
            gaps=[] if is_sufficient else ["더 많은 출처 필요"],
            additional_queries=[],
            reasoning="자동 평가 (Gemini 미사용)",
        )


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


def _parse_json(text: str) -> Optional[dict]:
    text = re.sub(r'^```(?:json)?\n?', '', text.strip())
    text = re.sub(r'\n?```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None
