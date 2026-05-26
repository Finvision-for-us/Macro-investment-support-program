from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from app.deep_research.config import (
    GEMINI_API_KEY, GEMINI_PRO_MODEL, GEMINI_FLASH_MODEL,
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

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 1단계 프롬프트: Gemini Pro → 마크다운 서술 보고서
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

출처 품질 기준 (보고서 신뢰도 적용):
- Tier 1 (규제 공시): sec.gov, dart.fss.or.kr, csrc.gov.cn, sse.com.cn, szse.cn, szse.com.cn, hkexnews.hk, fsc.go.kr, jpx.co.jp, edinet-fsa.go.jp, esma.europa.eu, ec.europa.eu, federalreserve.gov, pbc.gov.cn 등 — 사실 주장의 최고 근거
- Tier 2 (Tier-1 미디어): reuters.com, apnews.com, ft.com, wsj.com, nikkei.com, bloomberg.com, caixin.com, scmp.com, yonhapnews.co.kr — 교차확인 가능
- Tier 3 (전문 분석): cnbc.com, marketwatch.com, techcrunch.com — 참고용
- Tier 4 (자동생성/블로그): stockinsights.ai, pitchgrade.com, stockanalysis.com, simplywall.st 등 — 이 출처만 있으면 "[추가 검증 필요]" 표시 필수

다국가·다관할 출처 처리 규칙:
- 중국 공시(CSRC/SSE/SZSE/HKEx): 중국어 원본과 영문 번역이 모두 있으면 중국어 원본 수치를 우선하라
- 한국 공시(DART): 한국어 공시와 영문 보도가 모순되면 DART 원본 수치를 우선하라
- 일본 공시(EDINET/JPX): 일본어 원본 수치를 우선하라
- 미국·비미국 규제 기관 간 설명이 다를 경우 해당 관할 기관의 원본 공시를 우선하라
- cross-border 거래(예: 중국 기업 + 미국 규제)는 두 관할 공시를 모두 명시하고 출처 국가를 [US] [CN] 태그로 구분하라

보고서 형식 (마크다운):
- 맨 앞 첫 번째 섹션은 반드시 ## 핵심 요약 으로 시작 (2~3문단)
- 이후 각 섹션은 ## {섹션 제목} 형식의 헤더로 시작
- 각 섹션 본문은 끊기지 않는 단락형 서술로 작성 — 인과관계(A→B→C)와 논리 흐름을 충분히 전개
- 출처는 본문 inline에 [source: URL] 형식으로 삽입
- 추론은 [추론] 태그로 명시

마크다운만 출력. JSON 블록 없음."""


# ─────────────────────────────────────────────────────────────
# 2단계 프롬프트: Gemini Flash → 구조 메타데이터 추출
# ─────────────────────────────────────────────────────────────
EXTRACTION_PROMPT = """아래 마크다운 보고서에서 구조화된 메타데이터만 추출하세요.
본문 재작성 금지. 마크다운에 이미 있는 내용만 구조화하세요.

[마크다운 보고서]
{markdown_report}

추출 규칙:
- timeline: 보고서 본문에 명시된 날짜-사건 쌍만 포함. 본문에 없는 날짜/사건 추가 금지
- key_findings: 본문의 핵심 주장을 그대로 요약. 새 주장 생성 금지
- coverage: 본문에서 언급된 출처 유형과 한계를 그대로 반영

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
  }}
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

    def _get_model(self):
        if self._model is None and GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                self._model = genai.GenerativeModel(GEMINI_PRO_MODEL)
                logger.info(f"[synthesizer] Gemini Pro 초기화: {GEMINI_PRO_MODEL}")
            except Exception as e:
                logger.error(f"[synthesizer] Gemini 초기화 실패: {e}")
        return self._model

    def _get_flash_model(self):
        """2단계 추출 전용 Flash 인스턴스."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            return genai.GenerativeModel(GEMINI_FLASH_MODEL)
        except Exception as e:
            logger.error(f"[synthesizer] Flash 초기화 실패: {e}")
            return None

    def _get_flash_fallback(self):
        """Pro 불가 시 Flash 폴백 (1단계 재시도 / 자기 검증)."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_FLASH_MODEL)
            logger.warning(f"[synthesizer] Pro 불가 → Flash 폴백: {GEMINI_FLASH_MODEL}")
            return model
        except Exception as e:
            logger.error(f"[synthesizer] Flash 폴백도 실패: {e}")
            return None

    async def _generate_narrative(
        self,
        query: str,
        sections_str: str,
        sources_text: str,
        model,
    ) -> Optional[str]:
        """1단계: Gemini Pro로 마크다운 서술 보고서 생성."""
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
                logger.warning("[synthesizer] Pro 할당량 초과 → Flash로 1단계 재시도")
                flash = self._get_flash_fallback()
                if flash is None:
                    return None
                try:
                    response = await asyncio.to_thread(
                        flash.generate_content,
                        prompt,
                        request_options={"timeout": 300},
                    )
                    text = response.text.strip()
                    self._tokens_used += len(text) // 4
                    return text
                except Exception as e2:
                    logger.error(f"[synthesizer] Flash 1단계도 실패: {e2}")
                    return None
            logger.error(f"[synthesizer] 1단계 생성 실패: {e}")
            return None

    async def _extract_metadata(self, markdown_report: str) -> dict:
        """2단계: Gemini Flash로 timeline/key_findings/coverage JSON 추출."""
        flash = self._get_flash_model()
        if flash is None:
            return {}

        prompt = EXTRACTION_PROMPT.format(
            markdown_report=markdown_report[:20_000],
        )
        try:
            response = await asyncio.to_thread(
                flash.generate_content,
                prompt,
                request_options={"timeout": 120},
            )
            result = _parse_json(response.text.strip())
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
        """방어선 5: Gemini Flash로 보고서 자기 검증 패스."""
        flash = self._get_flash_fallback()
        if flash is None:
            return data

        raw_texts = raw_storage.all_texts_combined(max_chars=60_000)
        try:
            import json as _json
            verify_prompt = VERIFY_PROMPT.format(
                report_json=_json.dumps(data, ensure_ascii=False)[:8000],
                raw_sources=raw_texts[:10_000],
            )
            resp = await asyncio.to_thread(
                flash.generate_content,
                verify_prompt,
                request_options={"timeout": 120},
            )
            verified = _parse_json(resp.text.strip())
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
    ) -> DeepResearchResponse:
        """2단계 보고서 생성: 1단계(서술) → 2단계(구조 추출) + 검증."""
        model = self._get_model()

        all_sources = _build_source_list(contents, search_results)
        sections_str = "\n".join(f"- {s}" for s in required_sections)
        sources_text = _format_sources_for_prompt(contents, max_chars=120_000)

        if model is None:
            return self._fallback_response(query, all_sources, metadata, job_id)

        try:
            # ── 1단계: Gemini Pro → 마크다운 서술 보고서 ──
            markdown_report = await self._generate_narrative(
                query, sections_str, sources_text, model
            )
            if not markdown_report:
                return self._fallback_response(query, all_sources, metadata, job_id)

            # ── 각주 번호 매핑: [source: URL] → [n] ──
            url_to_num = _build_footnote_map(markdown_report)
            if url_to_num:
                markdown_report = _apply_footnote_numbers(markdown_report, url_to_num)
                for src in all_sources:
                    num = url_to_num.get(src.url)
                    if num is not None:
                        src.ref_number = num

            # 마크다운 파싱 → summary + sections
            summary, sections_data = _parse_markdown_report(markdown_report)

            # ── 2단계: Gemini Flash → timeline/key_findings/coverage 추출 ──
            metadata_json = await self._extract_metadata(markdown_report)

            # 전체 데이터 조립
            data = {
                "summary": summary,
                "sections": sections_data,
                "timeline": metadata_json.get("timeline", []),
                "key_findings": metadata_json.get("key_findings", []),
                "coverage": metadata_json.get("coverage", {}),
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

            # ── 방어선 5: 자기 검증 패스 (Flash로 비용 절약) ──
            if raw_storage and len(raw_storage) > 0:
                data = await self._self_verify(data, raw_storage, model)

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

            key_findings = [
                KeyFinding(
                    finding=f.get("finding", ""),
                    confidence=ConfidenceLevel(f.get("confidence", "medium")),
                    sources=f.get("sources", []),
                )
                for f in data.get("key_findings", [])
            ]

            # coverage: pipeline 전처리(관할 감지) 결과와 LLM 추출 결과 병합
            coverage_data = data.get("coverage", {})
            llm_coverage = CoverageInfo(
                checked=coverage_data.get("checked", []),
                unchecked=coverage_data.get("unchecked", []),
                notes=coverage_data.get("notes", ""),
            ) if coverage_data else None

            if coverage and llm_coverage:
                # 파이프라인 관할 커버리지 우선, LLM 분석 내용 보조 추가
                merged_notes = coverage.notes
                if llm_coverage.notes:
                    merged_notes += " | " + llm_coverage.notes
                coverage = CoverageInfo(
                    checked=list(dict.fromkeys(coverage.checked + llm_coverage.checked)),
                    unchecked=list(dict.fromkeys(coverage.unchecked + llm_coverage.unchecked)),
                    notes=merged_notes,
                )
            elif llm_coverage:
                coverage = llm_coverage

            metadata.total_sources = len(all_sources)
            metadata.gemini_tokens_used += self._tokens_used

            return DeepResearchResponse(
                job_id=job_id,
                query=query,
                summary=data.get("summary", ""),
                sections=sections,
                timeline=sorted(timeline, key=lambda x: x.date),
                key_findings=key_findings,
                sources=all_sources,
                coverage=coverage,
                metadata=metadata,
                status=JobStatus.DONE,
            )

        except Exception as e:
            logger.error(f"[synthesizer] 합성 실패: {e}")
            return self._fallback_response(query, all_sources, metadata, job_id)

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
            domain = urlparse(c.url).netloc.lstrip("www.")
            _, credibility = score_url(c.url)
            sources.append(SourceInfo(
                url=c.url, title=c.title, domain=domain, credibility=credibility
            ))

    for r in search_results:
        if r.url and r.url not in seen:
            seen.add(r.url)
            domain = urlparse(r.url).netloc.lstrip("www.")
            _, credibility = score_url(r.url)
            sources.append(SourceInfo(
                url=r.url, title=r.title, domain=domain, credibility=credibility
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
