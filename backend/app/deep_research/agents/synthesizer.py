from __future__ import annotations
import asyncio
import copy
import json
import logging
import re
from typing import Awaitable, Callable, Literal, Optional

from pydantic import BaseModel

from app.deep_research.common import domain_of, parse_json_object

from app.deep_research.config import (
    GEMINI_API_KEY, DEEP_RESEARCH_SYNTH_MODEL, DEEP_RESEARCH_EXTRACT_MODEL,
    DEEP_RESEARCH_VERIFY_MODEL,
    PRO_INPUT_COST, PRO_OUTPUT_COST,
)
from app.deep_research.models import (
    ExtractedContent, SearchResult,
    DeepResearchResponse, ReportSection, TimelineEvent,
    KeyFinding, SourceInfo, ResearchMetadata, CoverageInfo,
    ConfidenceLevel, CredibilityLevel, JobStatus,
)
from app.deep_research.storage.raw_sources import RawSourceStorage
from app.deep_research.agents.source_matcher import source_matcher
from app.deep_research.agents.cross_checker import cross_checker
from app.deep_research.agents.evidence_ranker import score_url
from app.deep_research.agents import numeric_consistency
from app.deep_research.agents import report_sanitizer
from app.deep_research.agents.claim_ledger import build_claim_ledger
from app.deep_research.agents.calculation_ledger import build_calculation_ledger
from app.deep_research.agents.scenario_validator import build_scenario_analysis
from app.deep_research import llm_client

logger = logging.getLogger(__name__)


# ── 구조화 출력 스키마 (EXTRACTION_PROMPT / VERIFY_PROMPT의 JSON 형식과 1:1) ──
# 2단계 추출의 key_findings가 자유텍스트 파싱에서 0~4개로 변동하던 문제(라이브 실측)를
# response_schema 강제로 제거한다. confidence는 Literal로 API 레벨에서 enum 강제 —
# 'none' 같은 값이 리서치 전체를 폴백시키던 버그 계열의 원천 차단.
class TimelineOut(BaseModel):
    date: str = ""
    event: str = ""
    source: str = ""


class FindingOut(BaseModel):
    finding: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    sources: list[str] = []


class MetricOut(BaseModel):
    metric_name: str = ""
    value: str = ""
    unit: str = ""
    entity: str = ""
    scope: str = ""
    period: str = ""
    period_type: str = ""
    as_of: Optional[str] = None
    basis: str = ""
    currency: Optional[str] = None
    source_id: Optional[str] = None


class CalculationOut(BaseModel):
    calculation_type: Literal[
        "derived", "mechanical_sensitivity", "forecast", "scenario"
    ] = "derived"
    description: str = ""
    formula: str = ""
    formula_expression: str = ""
    inputs: list[MetricOut] = []
    assumptions: list[str] = []
    required_alignment: list[str] = []
    output: Optional[MetricOut] = None


class ScenarioCaseOut(BaseModel):
    name: Literal["bull", "base", "bear"] = "base"
    probability: float = 0.0
    assumptions: list[str] = []
    outputs: list[MetricOut] = []
    invalidation_triggers: list[str] = []
    evidence_source_ids: list[str] = []


class CoverageOut(BaseModel):
    checked: list[str] = []
    unchecked: list[str] = []
    notes: str = ""


class MetadataOut(BaseModel):
    timeline: list[TimelineOut] = []
    key_findings: list[FindingOut] = []
    coverage: CoverageOut = CoverageOut()
    calculations: list[CalculationOut] = []
    scenarios: list[ScenarioCaseOut] = []


class SectionOut(BaseModel):
    title: str = ""
    content: str = ""
    sources: list[str] = []


class VerifiedReportOut(BaseModel):
    summary: str = ""
    sections: list[SectionOut] = []
    timeline: list[TimelineOut] = []
    key_findings: list[FindingOut] = []
    coverage: CoverageOut = CoverageOut()
    calculations: list[CalculationOut] = []
    scenarios: list[ScenarioCaseOut] = []


# ─────────────────────────────────────────────────────────────
# 1단계 프롬프트: 역할별 synth 모델 → 마크다운 서술 보고서
# ─────────────────────────────────────────────────────────────
NARRATIVE_PROMPT = """당신은 세계 최고 수준의 금융 리서치 애널리스트이자 팩트체커입니다.
아래 수집된 자료를 바탕으로 사용자 질의에 대한 심층 분석 보고서를 마크다운으로 작성하세요.

[사용자 질의]
{query}

[보고서 구성 섹션]
{sections}

[수집된 자료 — 이 텍스트가 유일한 정보 원천]
{sources_text}

━━━━━━━━━━━━━━━━━━━━━━━
환각 무관용 정책 (HALLUCINATION ZERO POLICY)
━━━━━━━━━━━━━━━━━━━━━━━
당신이 환각을 일으키면 이 시스템 전체가 무너집니다.

[핵심 데이터 — 엄격 모드]
다음 항목은 수집된 자료에 글자 그대로 존재해야만 포함 가능:
- 숫자 (매출, 가격, 비율, 주가, EPS): 원본에서 그대로 복사. 단위 변환 금지
- 날짜: 원본에 있는 날짜만
- 인물명, 직책: 원본 표기 그대로
- 기업명, 거래 상대방: 원본 표기 그대로
- 직접 인용문 ("..."): 원본에 그 문장이 있어야 함
→ 원본에 없으면 무조건 삭제. "원본 확인 필요"라고 쓰거나 생략.

[해석/추론 — 표시 모드]
다음은 [추론] 태그를 붙여서 포함 가능:
- 시장 영향 분석
- 경쟁 구도 평가
- 미래 전망
→ 반드시 "[추론]" 태그로 시작할 것

절대 규칙:
1. raw_sources에 없는 사실은 추가하지 마라 — 사전 학습 지식으로 보충 금지
2. 숫자는 원본 텍스트에서 직접 복사하라. 절대 계산하거나 변환하지 마라
3. 모르는 것은 "정보 부족"으로 적어라
4. 추론은 [추론] 태그로 명시하라
5. 각 주장 끝에 [source: URL] 형식으로 출처를 반드시 명시하라
6. 모순되는 정보가 있으면 양쪽 다 명시하고 각각 출처를 밝혀라
7. 짧고 정확한 보고서 > 길고 환각 있는 보고서 — 정보가 부족하면 짧게 써라
8. [SEC Form 4 데이터 보존 규칙] 원본에 "【SEC Form 4" 또는 "거래 내역:" 섹션이 있으면:
   - 임원 직책은 Form 4 원본 표기 그대로 사용 (예: "Chief Executive Officer"). "추정" 금지
   - 거래 성격 분류(예: "세금납부 원천징수 (sell-to-cover)", "RSU 베스팅", "Rule 10b5-1 사전 계획 매매")는 원본 분류를 그대로 유지. "매도" 또는 "매수"로 단순화 금지
   - 거래 수량, 주당 가격, 거래 후 보유량은 원본 수치 그대로 사용
   - 각주(10b5-1 계획, 세금납부 목적 등) 내용을 반드시 포함
   - 출처를 [source: sec.gov URL]로 명시

검증 체크리스트 (각 문장 작성 전 확인):
□ 이 문장의 근거가 수집된 자료 어딘가에 있는가?
□ 숫자/날짜가 원본에 글자 그대로 있는가?
□ 인용문이 실제 출처에 존재하는가?
□ 추론이라면 [추론] 태그를 붙였는가?
실패한 문장은 삭제하거나 [unverified] 태그를 붙여라.

출처 품질 기준 (보고서 신뢰도 적용 — sources/source_registry.py의 단일 레지스트리와 동기화됨):
- Tier 1 (규제 공시): sec.gov, dart.fss.or.kr, csrc.gov.cn, sse.com.cn, szse.cn, hkexnews.hk, fsc.go.kr, jpx.co.jp, edinet-fsa.go.jp, esma.europa.eu, federalreserve.gov, pbc.gov.cn 등 — 사실 주장의 최고 근거
- Tier 2 (Tier-1 미디어): reuters.com, apnews.com, bloomberg.com, ft.com, wsj.com, nikkei.com, nytimes.com, bbc.com, caixin.com, scmp.com, yonhapnews.co.kr — 교차확인 가능
- Tier 3 (전문 분석): cnbc.com, marketwatch.com, techcrunch.com, barrons.com — 참고용
- Tier 4 (자동생성/루머/소셜): stockinsights.ai, pitchgrade.com, stockanalysis.com, simplywall.st, seekingalpha.com, fool.com, benzinga.com, reddit.com 등 — 이 출처만 있으면 "[추가 검증 필요]" 표시 필수

다국가·다관할 출처 처리 규칙:
- 중국 공시(CSRC/SSE/SZSE/HKEx): 중국어 원본과 영문 번역이 모두 있으면 중국어 원본 수치를 우선하라
- 한국 공시(DART): 한국어 공시와 영문 보도가 모순되면 DART 원본 수치를 우선하라
- 일본 공시(EDINET/JPX): 일본어 원본 수치를 우선하라
- 미국·비미국 규제 기관 간 설명이 다를 경우 해당 관할 기관의 원본 공시를 우선하라
- cross-border 거래(예: 중국 기업 + 미국 규제)는 두 관할 공시를 모두 명시하고 출처 국가를 [US] [CN] 태그로 구분하라

보고서 형식 (마크다운):
- 맨 앞 첫 번째 섹션은 반드시 ## 핵심 요약 으로 시작 (2~3문단)
- 이후 각 섹션은 ## {{섹션 제목}} 형식의 헤더로 시작
- 각 섹션 본문은 끊기지 않는 단락형 서술로 작성 — 인과관계(A→B→C)와 논리 흐름을 충분히 전개
- 출처는 본문 inline에 [source: URL] 형식으로 삽입
- 추론은 [추론] 태그로 명시
- 사용자 질문이 투자 전망·밸류에이션·향후 실적을 요구할 때만 Bull/Base/Bear 시나리오를 작성하라.
  · 세 시나리오는 동일한 결과 지표·단위·범위·기간으로 비교
  · 각 시나리오에 확률, 명시적 가정, 무효화 조건, 근거 출처 포함
  · 자료가 부족하면 억지로 수치 시나리오를 만들지 말고 정보 부족으로 명시
- 맨 마지막 섹션은 반드시 ## 미검증·불확실 항목 으로 작성. 다음을 항목(-)으로 솔직히 나열:
  · 수집 자료로 확인하지 못한 사실, 공식 출처로 교차검증되지 않은 주장
  · 누락된 데이터, 접근하지 못한 관할/거래소 공시, 남은 의문점
  확인 못 한 것을 숨기거나 얼버무리지 말 것. 정말 없으면 "- 특이 미검증 항목 없음"이라고 명시.

마크다운만 출력. JSON 블록 없음."""


# ─────────────────────────────────────────────────────────────
# 2단계 프롬프트: 역할별 extract 모델 → 구조 메타데이터 추출
# ─────────────────────────────────────────────────────────────
EXTRACTION_PROMPT = """아래 마크다운 보고서에서 구조화된 메타데이터만 추출하세요.
본문 재작성 금지. 마크다운에 이미 있는 내용만 구조화하세요.

[마크다운 보고서]
{markdown_report}

추출 규칙:
- timeline: 보고서 본문에 명시된 날짜-사건 쌍만 포함. 본문에 없는 날짜/사건 추가 금지
- key_findings: 본문의 핵심 주장을 그대로 요약. 새 주장 생성 금지
- coverage: 본문에서 언급된 출처 유형과 한계를 그대로 반영
- calculations: 보고서가 실제로 수행하거나 인용한 계산만 추출. 계산이 없으면 빈 배열
- scenarios: 보고서에 Bull/Base/Bear가 실제로 있을 때만 세 시나리오를 추출. 없으면 빈 배열
- 특정 기업·산업 지식을 가정하지 말고 모든 입력에 entity/scope/period/period_type/as_of/basis/unit/currency/source_id를 기록
- required_alignment: 이 계산에서 입력끼리 반드시 같아야 하는 의미 차원만 다음 중 선택:
  entity, scope, period, period_type, as_of, basis, currency
- 실제치·가이던스·컨센서스, 전체·사업부, 분기·연간을 임의로 같은 값처럼 취급 금지
- formula_expression: input_0, input_1 순서와 + - * / 괄호만 사용하는 기계 실행식.
  원문 계산을 정확히 표현할 수 없으면 빈 문자열. 예: "input_0 * (1 + input_1)"

confidence 기준:
- high: 3개 이상 독립 출처에서 교차확인된 사실
- medium: 1~2개 출처, 신뢰도 높은 기관 (SEC/Reuters 등)
- low: 단일 출처, 신뢰도 낮거나 [추론]

다음 JSON만 출력:
{{
  "timeline": [
    {{"date": "YYYY-MM-DD 또는 YYYY-MM", "event": "사건 설명 [source: URL]", "source": "url"}}
  ],
  "key_findings": [
    {{"finding": "핵심 발견사항 [source: URL]", "confidence": "high 또는 medium 또는 low", "sources": ["url1", "url2"]}}
  ],
  "coverage": {{
    "checked": ["출처 유형/기관명 — 확인된 내용 요약"],
    "unchecked": ["출처 유형/기관명 — 미확인 이유"],
    "notes": "이번 리서치의 커버리지 한계 요약"
  }},
  "calculations": [
    {{
      "calculation_type": "derived | mechanical_sensitivity | forecast | scenario",
      "description": "계산 목적",
      "formula": "기호가 아닌 사람이 재현 가능한 산식",
      "formula_expression": "input_0 * input_1 형태의 실행식 또는 빈 문자열",
      "inputs": [{{
        "metric_name": "입력 지표명", "value": "원문 값", "unit": "단위",
        "entity": "대상 기업/경제주체", "scope": "전체/사업부/제품/지역 등",
        "period": "회계·달력 기간", "period_type": "quarter/annual/ttm/ytd/snapshot 등",
        "as_of": "YYYY-MM-DD 또는 null", "basis": "actual/guidance/consensus/GAAP/non-GAAP 등",
        "currency": "USD 등 또는 null", "source_id": "원문 URL"
      }}],
      "assumptions": ["명시적 가정"],
      "required_alignment": ["entity", "scope", "period"],
      "output": {{
        "metric_name": "출력 지표명", "value": "결과", "unit": "단위",
        "entity": "대상", "scope": "범위", "period": "기간",
        "period_type": "기간 유형", "as_of": null, "basis": "derived",
        "currency": "USD 등 또는 null", "source_id": null
      }}
    }}
  ],
  "scenarios": [
    {{
      "name": "bull | base | bear",
      "probability": 0.0,
      "assumptions": ["명시적 가정"],
      "outputs": [{{
        "metric_name": "목표 지표", "value": "값", "unit": "단위",
        "entity": "대상", "scope": "범위", "period": "기간",
        "period_type": "annual/quarter/snapshot 등", "as_of": null,
        "basis": "scenario", "currency": "USD 등 또는 null", "source_id": null
      }}],
      "invalidation_triggers": ["이 시나리오가 무효가 되는 관측 조건"],
      "evidence_source_ids": ["근거 URL"]
    }}
  ]
}}

JSON만 출력. 마크다운 없음."""


VERIFY_PROMPT = """당신은 팩트체킹 전문가입니다. 아래 보고서의 각 주장이 제공된 원본 자료에 실제로 있는지 검증하세요.

[검증할 보고서]
{report_json}

[원본 자료]
{raw_sources}

검증 규칙:
1. 숫자/날짜/인물명: 원본에 글자 그대로 있어야 함 — 없으면 [unverified] 태그
2. [추론] 태그가 없는 분석/전망 문장: 원본 근거 없으면 [추론] 태그 추가
3. 직접 인용("..."): 원본에 그 문장 없으면 삭제하고 "원본 확인 필요"로 대체
4. 출처 URL이 없는 핵심 주장: [source: 미확인] 표시

발견한 문제:
- 원본에 없는 숫자/날짜: [목록]
- 검증 실패 인용문: [목록]
- 추론 태그 누락: [목록]

수정된 보고서를 동일한 JSON 형식으로 반환하세요.
수정이 없으면 원본 JSON 그대로 반환.
JSON만 출력."""


class Synthesizer:
    """수집된 정보를 2단계(서술 생성 → 구조 추출)로 합성하여 최종 보고서 생성."""

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
                self._model = genai.GenerativeModel(DEEP_RESEARCH_SYNTH_MODEL)
                logger.info(f"[synthesizer] Gemini 모델 초기화: {DEEP_RESEARCH_SYNTH_MODEL}")
            except Exception as e:
                logger.error(f"[synthesizer] Gemini 초기화 실패: {e}")
        return self._model

    def _get_extract_model(self):
        """2단계 구조 추출 전용 모델."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            return genai.GenerativeModel(DEEP_RESEARCH_EXTRACT_MODEL)
        except Exception as e:
            logger.error(f"[synthesizer] 메타데이터 추출 모델 초기화 실패: {e}")
            return None

    def _get_verify_model(self, fallback: bool = False):
        """검증 모델 인스턴스. fallback=True이면 합성 재시도 로그를 남긴다."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(DEEP_RESEARCH_VERIFY_MODEL)
            if fallback:
                logger.warning(
                    f"[synthesizer] synth 모델 불가 → verify 모델 폴백: "
                    f"{DEEP_RESEARCH_VERIFY_MODEL}"
                )
            else:
                logger.info(f"[synthesizer] verify 모델 초기화: {DEEP_RESEARCH_VERIFY_MODEL}")
            return model
        except Exception as e:
            logger.error(f"[synthesizer] verify 모델 폴백도 실패: {e}")
            return None

    async def _generate_narrative(
        self,
        query: str,
        sections_str: str,
        sources_text: str,
        model,
    ) -> Optional[str]:
        """1단계: 역할별 synth 모델로 마크다운 서술 보고서 생성."""
        prompt = NARRATIVE_PROMPT.format(
            query=query,
            sections=sections_str,
            sources_text=sources_text,
        )
        try:
            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
                request_options={"timeout": 300},
            )
            text = response.text.strip()
            self._tokens_used += len(text) // 4
            logger.info(f"[synthesizer] 1단계 마크다운 생성 완료 ({len(text)} chars)")
            return text
        except Exception as e:
            if "quota" in str(e).lower() or "429" in str(e):
                logger.warning("[synthesizer] synth 모델 할당량 초과 → verify 모델로 1단계 재시도")
                verify_model = self._get_verify_model(fallback=True)
                if verify_model is None:
                    return None
                try:
                    response = await asyncio.to_thread(
                        verify_model.generate_content,
                        prompt,
                        request_options={"timeout": 300},
                    )
                    text = response.text.strip()
                    self._tokens_used += len(text) // 4
                    return text
                except Exception as e2:
                    logger.error(f"[synthesizer] verify 모델 1단계도 실패: {e2}")
                    return None
            logger.error(f"[synthesizer] 1단계 생성 실패: {e}")
            return None

    async def _extract_metadata(self, markdown_report: str) -> dict:
        """2단계: 역할별 extract 모델로 timeline/key_findings/coverage JSON 추출."""
        prompt = EXTRACTION_PROMPT.format(
            markdown_report=markdown_report[:20_000],
        )

        # ── 1차: 구조화 출력 — key_findings 추출 변동(0~4개)의 원인이던 파싱 제거 ──
        sres = await llm_client.generate_structured(
            prompt, MetadataOut, DEEP_RESEARCH_EXTRACT_MODEL,
            timeout_s=120, fallback_model=DEEP_RESEARCH_VERIFY_MODEL, tag="synthesizer",
        )
        if sres is not None:
            self._tokens_used += sres.output_tokens
            logger.info(
                f"[synthesizer] 2단계 메타데이터 추출(구조화): "
                f"findings {len(sres.data.key_findings)}, timeline {len(sres.data.timeline)}"
            )
            return sres.data.model_dump()

        # ── 2차(레거시): 자유텍스트 + 정규식 파싱 — 구조화 실패 시 동작 보존 ──
        extract_model = self._get_extract_model()
        if extract_model is None:
            return {}
        try:
            response = await asyncio.to_thread(
                extract_model.generate_content,
                prompt,
                request_options={"timeout": 120},
            )
            result = parse_json_object(response.text.strip())
            if result and isinstance(result, dict):
                logger.info("[synthesizer] 2단계 메타데이터 추출 완료")
                return result
            logger.warning("[synthesizer] 2단계 JSON 파싱 실패, 빈 메타데이터 사용")
        except Exception as e:
            logger.warning(f"[synthesizer] 2단계 추출 실패 (빈 메타데이터 사용): {e}")
        return {}

    async def _self_verify(
        self,
        data: dict,
        raw_storage: RawSourceStorage,
        model,
    ) -> dict:
        """방어선 5: 역할별 verify 모델로 보고서 자기 검증 패스.

        컨텍스트 상한 근거(2026-07-20 실측): 종전 원문 10k자/리포트 8k자 제한이
        [unverified] 남발의 주범 — 수치가 원문에 있어도 컨텍스트 밖이면 미확인
        처리됐다. thinking 모델 전환으로 긴 컨텍스트 비용이 감내 가능해져 확대
        (원문 150k자 ≈ 45k토큰 ≈ +$0.07/run).
        """
        raw_texts = raw_storage.all_texts_combined(max_chars=250_000)
        verify_prompt = VERIFY_PROMPT.format(
            report_json=json.dumps(data, ensure_ascii=False)[:30_000],
            raw_sources=raw_texts[:150_000],
        )

        # ── 1차: 구조화 출력 — 검증 결과도 스키마 강제 (형식 이탈로 원본 폐기 방지) ──
        sres = await llm_client.generate_structured(
            verify_prompt, VerifiedReportOut, DEEP_RESEARCH_VERIFY_MODEL,
            timeout_s=120, tag="synthesizer",
        )
        if sres is not None:
            out = sres.data.model_dump()
            # 빈 껍데기(전 필드 기본값) 방어 — 검증 패스가 보고서를 지우면 안 된다
            if out.get("summary") or out.get("sections"):
                self._tokens_used += sres.output_tokens
                logger.info("[synthesizer] 자기 검증 패스 완료(구조화)")
                return out
            logger.warning("[synthesizer] 구조화 자기 검증이 빈 보고서 반환 — 원본 유지")
            return data

        # ── 2차(레거시): 자유텍스트 + 정규식 파싱 — 구조화 실패 시 동작 보존 ──
        verify_model = self._get_verify_model()
        if verify_model is None:
            return data
        try:
            resp = await asyncio.to_thread(
                verify_model.generate_content,
                verify_prompt,
                request_options={"timeout": 120},
            )
            verified = parse_json_object(resp.text.strip())
            if verified and isinstance(verified, dict):
                logger.info("[synthesizer] 자기 검증 패스 완료")
                return verified
        except Exception as e:
            logger.warning(f"[synthesizer] 자기 검증 실패 (원본 사용): {e}")
        return data

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def estimated_cost(self) -> float:
        return self._tokens_used * (PRO_OUTPUT_COST / 1_000_000)

    async def synthesize(
        self,
        query: str,
        contents: list[ExtractedContent],
        search_results: list[SearchResult],
        required_sections: list[str],
        metadata: ResearchMetadata,
        job_id: str,
        raw_storage: Optional[RawSourceStorage] = None,
        coverage: Optional[CoverageInfo] = None,  # pipeline 전처리에서 주입
        context: Optional[dict] = None,  # request.context — XBRL 원장 대조용 ticker
        on_draft: Optional[Callable[["DeepResearchResponse"], Awaitable[None]]] = None,
    ) -> DeepResearchResponse:
        """2단계 보고서 생성: 1단계(서술) → 2단계(구조 추출) + 검증.

        on_draft: 서술+메타데이터+출처검증까지 끝난 '초안'을 LLM 심사 전에
        먼저 전달하는 콜백(체감속도 개선 — 방어선 5 심사본으로 곧 교체).
        """
        model = self._get_model()

        all_sources = _build_source_list(contents, search_results)
        sections_str = "\n".join(f"- {s}" for s in required_sections)
        sources_text = _format_sources_for_prompt(contents, max_chars=120_000)

        if model is None:
            return self._fallback_response(query, all_sources, metadata, job_id)

        try:
            # ── 1단계: synth 모델 → 마크다운 서술 보고서 ──
            markdown_report = await self._generate_narrative(
                query, sections_str, sources_text, model
            )
            if not markdown_report:
                return self._fallback_response(query, all_sources, metadata, job_id)

            # ── 각주 번호 매핑: [source: URL] → [n] ──
            url_to_num = _build_footnote_map(markdown_report)
            if url_to_num:
                markdown_report = _apply_footnote_numbers(markdown_report, url_to_num)
                # 각주 무결성(방어선 6): 본문이 인용했지만 목록에 없는 URL을
                # 편입해 댕글링 각주([n]인데 목록에 없음)를 근본 차단.
                all_sources = report_sanitizer.ensure_cited_sources(
                    all_sources, url_to_num)
                report_sanitizer.assign_ref_numbers(all_sources, url_to_num)

            # 마크다운 파싱 → summary + sections
            summary, sections_data = _parse_markdown_report(markdown_report)

            # '미검증·불확실 항목' 섹션 → 구조화된 gap 리스트 (관측/프론트 노출용)
            unverified_gaps = _extract_gaps_from_sections(sections_data)

            # ── 2단계: extract 모델 → timeline/key_findings/coverage 추출 ──
            metadata_json = await self._extract_metadata(markdown_report)

            # 전체 데이터 조립
            data = {
                "summary": summary,
                "sections": sections_data,
                "timeline": metadata_json.get("timeline", []),
                "key_findings": metadata_json.get("key_findings", []),
                "coverage": metadata_json.get("coverage", {}),
                "calculations": metadata_json.get("calculations", []),
                "scenarios": metadata_json.get("scenarios", []),
            }

            # ── 방어선 2: Source-Claim 검증 (2단계 추출 후 적용) ──
            if raw_storage and len(raw_storage) > 0:
                src_texts = [s.text for s in raw_storage.all_sources()]
                verified_findings = []
                for f in data.get("key_findings", []):
                    result = source_matcher.verify_claim(f.get("finding", ""), src_texts)
                    if result["verified"]:
                        verified_findings.append(f)
                    elif result["unverified_facts"]:
                        f["finding"] = f"[unverified] {f.get('finding', '')}"
                        f["confidence"] = "low"
                        verified_findings.append(f)
                        logger.warning(f"[synthesizer] 미검증 수치: {result['unverified_facts']}")
                    else:
                        verified_findings.append(f)
                data["key_findings"] = verified_findings

            # ── ② 초안 즉시 방출 (LLM 심사 전) — 체감속도 개선 ──
            # 서술+메타데이터+출처검증까지 끝난 판본을 먼저 보여주고, 이후 방어선
            # 5(LLM 심사)+5.5+6+교차검증을 거친 '심사본'으로 교체된다. 초안도
            # 결정론 정리(각주/태그)만 적용해 깨끗하게 보여준다(LLM 콜 0).
            if on_draft is not None:
                try:
                    draft_data = copy.deepcopy(data)
                    try:
                        draft_data, _ = report_sanitizer.reconcile_and_sanitize(
                            draft_data, all_sources, url_to_num)
                    except Exception:
                        pass
                    draft = self._assemble_response(
                        query=query, job_id=job_id, data=draft_data,
                        all_sources=all_sources, pipeline_coverage=coverage,
                        unverified_gaps=unverified_gaps, cross_validation=[],
                        metadata=metadata, status=JobStatus.RUNNING,
                        raw_storage=raw_storage)
                    await on_draft(draft)
                except Exception as e:
                    logger.warning(f"[synthesizer] 초안 방출 실패(무시): {e}")

            # ── 방어선 5: 자기 검증 패스 ──
            if raw_storage and len(raw_storage) > 0:
                data = await self._self_verify(data, raw_storage, model)

                # ── 방어선 5.5: 수치 전용 결정론 재검증 — LLM 검증이 컨텍스트
                # 제한으로 놓친 수치를 '전체' 수집 원문과 대조해 태그 해소 ──
                try:
                    from app.deep_research.agents.numeric_reverifier import reverify_report
                    full_corpus = "\n".join(s.text for s in raw_storage.all_sources())
                    data, _removed, _kept = reverify_report(data, full_corpus)
                except Exception as e:
                    logger.warning(f"[synthesizer] 수치 재검증 실패(태그 유지): {e}")

            # ── 방어선 6: 출력 후처리 — 각주 무결성/태그 위치/깨진 잔재 정정 ──
            # (모든 LLM 패스가 끝난 최종 data에 적용. 순수 문자열 변환.)
            try:
                data, _san = report_sanitizer.reconcile_and_sanitize(
                    data, all_sources, url_to_num)
            except Exception as e:
                logger.warning(f"[synthesizer] 출력 후처리 실패(원문 유지): {e}")

            # ── 방어선 4: 다출처 교차검증 (cross_checker 실제 실행) → 응답 노출 ──
            cross_validation = self._cross_validate(data.get("key_findings", []), raw_storage)
            # ── 방어선 4b: 결정론적 수치 정합(pro-rata·세율·gross↔net)도 교차검증에 노출 ──
            cross_validation = list(dict.fromkeys(
                cross_validation + self._numeric_cross_validation(contents)
            ))
            # ── 방어선 4c: SEC XBRL 원장 대조 — 보고서 수치를 공시 원장 값과 대조 ──
            # (source_matcher의 '수집 텍스트 축자 존재'보다 한 단계 강한 검증.
            #  확인 전용 — 원장 미존재 수치는 침묵. LLM 무관여 순수 조회+비교.)
            ticker = (context or {}).get("ticker", "")
            if ticker:
                try:
                    from app.deep_research.agents.xbrl_ledger import verify_report_numbers
                    report_text = " ".join(
                        [data.get("summary", "")]
                        + [f.get("finding", "") for f in data.get("key_findings", [])]
                        + [s.get("content", "") for s in data.get("sections", [])]
                    )
                    ledger_lines = await verify_report_numbers(report_text, ticker)
                    if ledger_lines:
                        logger.info(f"[synthesizer] XBRL 원장 일치 {len(ledger_lines)}건 ({ticker})")
                        cross_validation = list(dict.fromkeys(cross_validation + ledger_lines))
                except Exception as e:
                    logger.warning(f"[synthesizer] XBRL 원장 대조 실패(무시): {e}")

            metadata.gemini_tokens_used += self._tokens_used
            return self._assemble_response(
                query=query, job_id=job_id, data=data, all_sources=all_sources,
                pipeline_coverage=coverage, unverified_gaps=unverified_gaps,
                cross_validation=cross_validation, metadata=metadata,
                status=JobStatus.DONE,
                raw_storage=raw_storage,
            )

        except Exception as e:
            logger.error(f"[synthesizer] 합성 실패: {e}")
            return self._fallback_response(query, all_sources, metadata, job_id)

    def _assemble_response(
        self, *, query: str, job_id: str, data: dict,
        all_sources: list, pipeline_coverage: Optional[CoverageInfo],
        unverified_gaps: list, cross_validation: list,
        metadata: ResearchMetadata, status: JobStatus,
        raw_storage: Optional[RawSourceStorage] = None,
    ) -> DeepResearchResponse:
        """data(dict) → DeepResearchResponse. 초안·심사본 공용 조립기.

        coverage 병합은 pipeline_coverage 원본을 불변으로 두고 매번 새로 계산
        (초안·심사본 2회 호출돼도 이중 병합되지 않게).
        """
        sections = [
            ReportSection(
                title=s.get("title", ""),
                content=s.get("content", ""),
                sources=s.get("sources", []),
            )
            for s in data.get("sections", [])
        ]
        timeline = [
            TimelineEvent(
                date=t.get("date", ""),
                event=t.get("event", ""),
                source=t.get("source", ""),
            )
            for t in data.get("timeline", [])
        ]
        # finding별 방어적 생성: 한 항목이 깨져도 전체 폴백되지 않게 그 항목만 스킵.
        key_findings = []
        for f in data.get("key_findings", []):
            try:
                key_findings.append(KeyFinding(
                    finding=f.get("finding", ""),
                    confidence=_coerce_confidence(f.get("confidence")),
                    sources=f.get("sources", []) or [],
                ))
            except Exception as e:
                logger.warning(f"[synthesizer] key_finding 스킵(파싱 실패): {e}")

        # coverage: pipeline 전처리(관할 감지) + LLM 추출 병합 (순수 — 원본 불변)
        coverage_data = data.get("coverage", {})
        llm_coverage = CoverageInfo(
            checked=coverage_data.get("checked", []),
            unchecked=coverage_data.get("unchecked", []),
            notes=coverage_data.get("notes", ""),
        ) if coverage_data else None
        coverage = pipeline_coverage
        if pipeline_coverage and llm_coverage:
            merged_notes = pipeline_coverage.notes
            if llm_coverage.notes:
                merged_notes += " | " + llm_coverage.notes
            coverage = CoverageInfo(
                checked=list(dict.fromkeys(pipeline_coverage.checked + llm_coverage.checked)),
                unchecked=list(dict.fromkeys(pipeline_coverage.unchecked + llm_coverage.unchecked)),
                notes=merged_notes,
            )
        elif llm_coverage:
            coverage = llm_coverage

        metadata.total_sources = len(all_sources)
        claim_ledger = build_claim_ledger(
            job_id, data.get("key_findings", []), raw_storage,
        )
        calculation_ledger = build_calculation_ledger(
            job_id, data.get("calculations", []),
        )
        scenario_analysis = build_scenario_analysis(
            job_id, data.get("scenarios", []),
        )
        eligible_claims = [
            claim.claim_text
            for claim in claim_ledger
            if claim.executive_summary_eligible
        ]
        safe_executive_summary = (
            "\n".join(f"- {text}" for text in eligible_claims)
            if eligible_claims
            else "검증 기준을 통과한 핵심 주장이 아직 없습니다. 전체 보고서와 미검증 항목을 확인하세요."
        )

        return DeepResearchResponse(
            job_id=job_id,
            query=query,
            summary=data.get("summary", ""),
            safe_executive_summary=safe_executive_summary,
            sections=sections,
            timeline=sorted(timeline, key=lambda x: x.date),
            key_findings=key_findings,
            sources=all_sources,
            coverage=coverage,
            unverified_gaps=unverified_gaps,
            cross_validation=cross_validation,
            claim_ledger=claim_ledger,
            calculation_ledger=calculation_ledger,
            scenario_analysis=scenario_analysis,
            metadata=metadata,
            status=status,
        )

    def _cross_validate(self, key_findings: list[dict], raw_storage) -> list[str]:
        """핵심 주장을 수집 원본과 다출처 교차검증해 사람이 읽을 문장 리스트로 반환.

        cross_checker(방어선 4)를 실제 실행한다. 새 사실을 만들지 않고, 각 주장이
        몇 개 출처에서 일치하는지 / 수치가 상충하는지만 기록한다(무할루시네이션).
        """
        if not raw_storage or len(raw_storage) == 0 or not key_findings:
            return []
        try:
            sources = raw_storage.all_sources()
        except Exception:
            return []
        if not sources:
            return []

        statements: list[str] = []
        for f in key_findings[:8]:
            claim = (f.get("finding") or "").strip()
            if not claim:
                continue
            short = re.sub(r'\[source:[^\]]*\]|\[\d+\]', '', claim).strip()
            short = short[:90] + ("…" if len(short) > 90 else "")
            try:
                result = cross_checker.cross_check(claim, sources)
            except Exception as e:
                logger.warning(f"[synthesizer] 교차검증 실패: {e}")
                continue
            agree_n = len(result.get("agreeing_sources") or [])
            conflicts = result.get("conflicting_sources") or []
            conf = result.get("confidence", "low")
            if conflicts:
                note = conflicts[0].get("note", "출처 간 수치 상충")
                statements.append(f"[수치 상충] {short} — {note}")
            elif agree_n >= 2:
                statements.append(f"[{agree_n}개 출처 일치·{conf}] {short}")
            elif agree_n == 1:
                statements.append(f"[단일 출처·미교차] {short}")
            else:
                statements.append(f"[교차 근거 부족] {short}")
        return statements

    def _numeric_cross_validation(self, contents: list[ExtractedContent]) -> list[str]:
        """결정론적 수치 정합 검사 결과를 교차검증 문장으로 변환.

        LLM 산술을 쓰지 않는다(코드가 계산). 실제 추출된 contents만 대상 →
        '열지 못한 값'은 상충 근거가 되지 않는다. 정합/상충 문장만 반환, 최대 8개.
        """
        if not contents:
            return []
        try:
            nres = numeric_consistency.analyze(contents)
        except Exception as e:
            logger.warning(f"[synthesizer] 수치정합 검사 실패(무시): {e}")
            return []
        lines = list(nres.consistent) + list(nres.conflicts)
        if lines:
            logger.info(
                f"[synthesizer] 수치정합 교차검증: 정합 {len(nres.consistent)}, "
                f"상충 {len(nres.conflicts)}"
            )
        return lines[:8]

    def _fallback_response(
        self,
        query: str,
        sources: list[SourceInfo],
        metadata: ResearchMetadata,
        job_id: str,
    ) -> DeepResearchResponse:
        return DeepResearchResponse(
            job_id=job_id,
            query=query,
            summary="Gemini API를 사용할 수 없어 요약을 생성하지 못했습니다. 수집된 출처를 직접 확인하세요.",
            sections=[],
            timeline=[],
            key_findings=[],
            sources=sources,
            metadata=metadata,
            status=JobStatus.DONE,
            error="Gemini API 불가",
        )


# ─────────────────────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────────────────────

_SUMMARY_TITLES = frozenset(["핵심 요약", "요약", "Executive Summary", "종합 요약", "Summary"])
_GAP_SECTION_HINTS = ("미검증", "불확실", "unverified", "uncertain", "limitation")
_GAP_NONE_MARKERS = ("특이 미검증 항목 없음", "미검증 항목 없음", "해당 없음", "없음")


def _extract_gaps_from_sections(sections_data: list[dict]) -> list[str]:
    """'미검증·불확실 항목' 섹션 본문을 구조화된 gap 리스트로 추출.

    시중 딥리서치 AI가 명시하는 '미검증 gap'을 FinVision 출력에도 반영하기 위함.
    불릿(-, •, *) 또는 줄 단위로 분해하고, '없음' 표기는 제외한다.
    """
    gaps: list[str] = []
    for s in sections_data:
        title = (s.get("title") or "").lower()
        if not any(h in title for h in _GAP_SECTION_HINTS):
            continue
        for line in (s.get("content") or "").splitlines():
            item = line.strip(" -•*\t")
            if not item:
                continue
            if any(m in item for m in _GAP_NONE_MARKERS):
                continue
            gaps.append(item)
    # 중복 제거(순서 유지)
    seen: set[str] = set()
    out: list[str] = []
    for g in gaps:
        k = g.lower()
        if k not in seen:
            seen.add(k)
            out.append(g)
    return out


def _build_footnote_map(markdown: str) -> dict[str, int]:
    """마크다운에서 [source: URL] 첫 출현 순서대로 번호 부여 → {url: n}."""
    url_to_num: dict[str, int] = {}
    counter = 0
    for url in re.findall(r'\[source:\s*(https?://[^\]]+)\]', markdown):
        url = url.strip()
        if url not in url_to_num:
            counter += 1
            url_to_num[url] = counter
    return url_to_num


def _apply_footnote_numbers(markdown: str, url_to_num: dict[str, int]) -> str:
    """[source: URL] → [n] 치환. 매핑에 없는 URL은 빈 문자열로 제거."""
    def _replace(m: re.Match) -> str:
        url = m.group(1).strip()
        num = url_to_num.get(url)
        return f"[{num}]" if num else ""
    return re.sub(r'\[source:\s*(https?://[^\]]+)\]', _replace, markdown)


def _extract_and_strip_sources(content: str) -> tuple[list[str], str]:
    """[source: URL] 토큰을 추출하고 본문에서 제거. 이중 공백/줄바꿈 정리."""
    urls = list(dict.fromkeys(
        re.findall(r'\[source:\s*(https?://[^\]]+)\]', content)
    ))
    cleaned = re.sub(r'\s*\[source:\s*https?://[^\]]+\]', '', content)
    cleaned = re.sub(r' {2,}', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return urls, cleaned.strip()


def _parse_markdown_report(markdown: str) -> tuple[str, list[dict]]:
    """## / ### 헤더 기준으로 summary와 sections 분리. 형식 이탈 시 폴백."""
    raw = markdown.strip()

    # 헤더가 하나도 없으면 전체를 summary로
    if not re.search(r'^#{2,3}\s', raw, re.MULTILINE):
        logger.warning("[synthesizer] 마크다운 헤더 없음 — 전체를 summary로 처리")
        return raw or "(보고서 내용 없음)", []

    blocks = re.split(r'\n(?=#{2,3}\s)', raw)
    preamble = ""
    summary = ""
    sections: list[dict] = []

    for block in blocks:
        header_match = re.match(r'^#{2,3}\s(.+?)\n', block)
        if not header_match:
            # 첫 헤더 이전 서두 텍스트
            if block.strip() and not preamble:
                preamble = block.strip()
            continue

        title = header_match.group(1).strip()
        content = block[header_match.end():].strip()

        # 제목에 요약 키워드가 포함되면 summary로 처리 (느슨한 매칭)
        is_summary = any(kw in title for kw in _SUMMARY_TITLES)
        if is_summary and not summary:
            _, cleaned = _extract_and_strip_sources(content)
            summary = cleaned
        else:
            urls, cleaned = _extract_and_strip_sources(content)
            sections.append({"title": title, "content": cleaned, "sources": urls})

    # 요약 섹션이 없으면 서두 → 첫 섹션 앞부분 순으로 폴백
    if not summary:
        if preamble:
            summary = preamble
        elif sections:
            summary = sections[0]["content"][:500]
        else:
            summary = "(요약 없음)"

    return summary, sections


def _build_source_list(
    contents: list[ExtractedContent],
    search_results: list[SearchResult],
) -> list[SourceInfo]:
    seen: set[str] = set()
    sources: list[SourceInfo] = []

    for c in contents:
        if c.url not in seen:
            seen.add(c.url)
            domain = domain_of(c.url)
            _, credibility = score_url(c.url)
            sources.append(SourceInfo(
                url=c.url, title=c.title, domain=domain, credibility=credibility,
                publisher=c.publisher, published_at=c.published_at,
                document_type=c.document_type, reporting_period=c.reporting_period,
                source_section=c.source_section, source_type=c.source_type,
            ))

    for r in search_results:
        if r.url and r.url not in seen:
            seen.add(r.url)
            domain = domain_of(r.url)
            _, credibility = score_url(r.url)
            sources.append(SourceInfo(
                url=r.url, title=r.title, domain=domain, credibility=credibility,
                publisher=r.publisher, published_at=r.published_date,
                document_type=r.document_type, reporting_period=r.reporting_period,
                source_section=r.source_section, source_type=r.source_type,
            ))
    return sources


def _format_sources_for_prompt(contents: list[ExtractedContent], max_chars: int = 150000) -> str:
    parts = []
    remaining = max_chars
    for i, c in enumerate(contents, 1):
        header = f"\n--- 출처 [{i}]: {c.title}\nURL: {c.url}\n"
        body = c.content[:min(3000, remaining - len(header))]
        part = header + body + "\n"
        if remaining - len(part) < 0:
            break
        parts.append(part)
        remaining -= len(part)
    return "".join(parts)


_CONFIDENCE_MAP = {
    "high": ConfidenceLevel.HIGH, "h": ConfidenceLevel.HIGH,
    "높음": ConfidenceLevel.HIGH, "상": ConfidenceLevel.HIGH,
    "medium": ConfidenceLevel.MEDIUM, "med": ConfidenceLevel.MEDIUM,
    "m": ConfidenceLevel.MEDIUM, "moderate": ConfidenceLevel.MEDIUM,
    "보통": ConfidenceLevel.MEDIUM, "중간": ConfidenceLevel.MEDIUM, "중": ConfidenceLevel.MEDIUM,
    "low": ConfidenceLevel.LOW, "l": ConfidenceLevel.LOW,
    "낮음": ConfidenceLevel.LOW, "하": ConfidenceLevel.LOW,
}


def _coerce_confidence(value) -> ConfidenceLevel:
    """LLM이 준 confidence 값을 안전하게 ConfidenceLevel로 변환.

    'none'/null/오탈자 등 알 수 없는 값이 와도 예외를 던지지 않고 MEDIUM으로.
    (enum 직접 생성이 ValueError를 내 리서치 전체가 폴백되던 버그 방지.)
    """
    try:
        s = str(value or "").strip().lower()
    except Exception:
        return ConfidenceLevel.MEDIUM
    return _CONFIDENCE_MAP.get(s, ConfidenceLevel.MEDIUM)
