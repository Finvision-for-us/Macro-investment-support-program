from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional

from app.deep_research.config import (
    GEMINI_API_KEY, GEMINI_FLASH_MODEL, GEMINI_LITE_MODEL,
    PARALLEL_API_KEY, TAVILY_API_KEYS,
)

logger = logging.getLogger(__name__)


# ── 프롬프트 ───────────────────────────────────────────────

SCOUT_QUERIES_PROMPT = """당신은 금융 리서치 전문가입니다.
아래 종목과 질문에 대해 사전 검색을 위한 쿼리를 생성하세요.

[종목] {ticker}
[질문] {query}

목적: 계획을 세우기 전에 실제 정보를 파악하기 위한 초기 검색.
규칙:
- 5개의 검색 쿼리 생성 (영어)
- 종목명, 거래소 코드, 최근 이슈, 관련 기관 등 다양한 각도로
- 한 줄에 하나씩, 번호/기호 없이 쿼리 텍스트만 출력

쿼리 5개:"""


SCOUT_FOLLOWUP_PROMPT = """당신은 금융 리서치 전문가입니다.
'{ticker}'에 대한 지금까지의 정찰 검색 결과({round_no}라운드 진행됨)가 아래에 있습니다.
사용자 질문에 제대로 답하는 리서치 계획을 세우기에 아직 부족한 부분을 찾아,
빈틈을 메우는 추적 검색 쿼리를 만드세요.

[종목] {ticker}
[사용자 질문] {query}

[지금까지의 정찰 결과]
{round1_results}

[이미 검색한 쿼리 — 중복 금지]
{prior_queries}

추적 관점 (해당되는 것 위주로):
- 기존 결과에 나온 구체적 사건/수치/파트너십의 원문·공식 발표 확인
- 기존 결과의 주장 중 검증이 필요한 것 (출처가 커뮤니티/블로그뿐인 주장)
- 사용자 질문의 측면 중 기존 결과가 전혀 다루지 못한 것
- 반대 관점/리스크 (호재 질문이면 악재도, 악재 질문이면 반박도)

규칙:
- 4개의 검색 쿼리 생성 (영어), 기존 쿼리와 중복 금지, 더 구체적으로
- 한 줄에 하나씩, 번호/기호 없이 쿼리 텍스트만 출력
- 단, 기존 결과가 이미 충분해서 더 검색할 것이 없으면 SATURATED 한 단어만 출력

추적 쿼리 4개 (또는 SATURATED):"""


PLAN_REVIEW_PROMPT = """당신은 수석 리서치 심사관입니다.
아래는 '{ticker}' 리서치 계획 초안과 그 근거가 된 정찰 증거입니다.
초안을 증거와 대조해 엄격히 심사하고, 결함을 보강한 최종본을 작성하세요.

[사용자 질문] {query}

[정찰 증거]
{scout_results}

[계획 초안]
{draft}

심사 관점:
1. 사용자 질문의 모든 측면이 조사 항목으로 커버되는가 — 빠진 측면이 있으면 항목 추가
2. 각 항목이 '무엇을 · 어디서 · 어떻게 확인할지' 구체적인가 — 막연한 항목은 구체화
3. 반대 가설/리스크 검증 항목이 있는가 — 호재 질문이면 악재 검증도, 악재 질문이면 반박 검증도
4. 증거에 없는 추측이 섞였는가 — 있으면 제거하거나 '검증 필요'로 표시
5. 항목 간 중복은 합치고, 근거 표기(← 근거: ...)가 빠진 항목은 보완

출력 형식:
**심사 반영** 섹션에 무엇을 보강/제거/구체화했는지 2-3줄로 쓴 뒤,
초안과 동일한 형식으로 최종 계획 전체를 출력하세요. 초안을 그대로 반복하지 말고
심사에서 지적된 결함이 실제로 고쳐진 버전을 출력하세요."""


SCOUT_PLAN_PROMPT = """당신은 전문 금융 리서치 플래너입니다.
아래는 '{ticker}'에 대한 실제 사전 검색 결과입니다. 이 결과를 분석해 리서치 계획을 세워주세요.

[종목] {ticker}
[사용자 질문] {query}

[사전 검색 결과 — 실제 수집된 데이터]
{scout_results}

[FinVision 보유 데이터]
{internal_context}

지시사항 (반드시 준수):
1. 위 검색 결과에 명시된 사실에만 근거하여 계획 수립
2. 특정 기관/거래소/규제기관을 계획에 포함할 때는, 검색 결과 어디서 해당 기관이 언급됐는지 근거를 밝힐 것
3. 검색 결과에 없는 정보를 추측해서 계획에 포함하지 말 것
4. 회사명, 티커 등 고유명사는 검색 결과에 나온 정확한 표기를 사용할 것
5. [고유명사 교정 — 검색 근거 기반, 범용 적용]
   검색 결과를 보고 사용자가 입력한 모든 고유명사(회사명, 자회사명, 제품명, 인물명, 지역명, 약칭, 오타 등)의 공식 표기를 확인하라.
   - 사용자 입력과 검색 결과의 공식 명칭이 다르면: 계획서 첫 섹션 "명칭 교정" 항목에 다음 형식으로 명시:
     "사용자 입력 'XXX' → 공식 명칭 'YYY' (근거: [어느 소스에서 어떻게 확인됐는지 한 줄])"
     이후 본문 전체에서 교정된 공식 명칭을 사용.
   - 검색 결과에서 공식 명칭을 확인할 수 없으면: 추측하지 말고 원본 유지 + "명칭 불확실 — 검색 근거 없음" 표시.
   - 이 규칙은 종목, 인물, 지역, 제품명 등 모든 고유명사에 동일하게 적용됨.
   - 확신이 없을 때는 "원본 유지 + 불확실 표시"가 "추측 교정"보다 낫다.
6. 계획서 본문에 https:// 로 시작하는 URL을 절대 쓰지 말 것. 출처는 도메인명(예: seekingalpha.com)이나 소스 유형(예: Tavily 검색, SEC EDGAR)으로만 표기.
7. 임원 주식 거래 관련 질문이면 조사 항목에 'SEC Form 4 직접 조회'를 반드시 포함하고, 출처를 SEC EDGAR(sec.gov)로 명시.

형식 (JSON 없이 한국어):

**리서치 계획: {ticker} — {query_summary}**

**사전 검색 분석**
- 명칭 교정: [사용자 입력 명칭과 실제 공식 명칭이 다르면 여기서 교정 명시. 동일하면 생략]
- 핵심 사실: [검색 결과에서 확인된 핵심 사실]
- 주요 이슈: [실제 데이터에서 확인된 쟁점]
- 불확실 사항: [검색 결과만으로 판단 불가한 부분]

**조사 항목**
1. [항목 제목]: [무엇을 조사할지] ← 근거: [검색 결과의 어느 내용 때문인지]
2. [항목 제목]: [무엇을 조사할지] ← 근거: [검색 결과의 어느 내용 때문인지]
(계속...)

**예상 소요**: 5-10분
**정찰에서 확인된 출처**: [정찰 검색에서 실제로 잡힌 도메인/유형만 — URL 표기 금지]
  (참고 문구를 그대로 덧붙일 것: "실행 시 이에 한정되지 않고 웹 전체 검색 + SEC EDGAR·거래소 공시·IR 직접 조회로 확장됩니다")
**FinVision 기존 데이터**: [활용 가능한 내부 데이터 항목]

이 계획으로 진행할까요? 수정하고 싶은 항목이 있으면 말씀해주세요."""


PLAN_PROMPT_NO_SCOUT = """당신은 전문 금융 리서치 플래너입니다.
사용자의 질문을 분석하여 심층 리서치 계획을 세워주세요.

[종목] {ticker}
[사용자 질문] {query}
[FinVision 보유 데이터 요약]
{internal_context}

다음 형식으로 응답하세요 (JSON 없이 자연스러운 한국어로):

**리서치 계획: {ticker} — {query_summary}**

1. [항목 제목]: [무엇을 조사할지 한 줄 설명]
2. [항목 제목]: [무엇을 조사할지 한 줄 설명]
...

**예상 소요**: 약 X분
**활용 데이터 소스**: [사용할 소스 목록]
**FinVision 기존 데이터**: [활용 가능한 내부 데이터 항목]

이 계획으로 진행할까요? 수정하고 싶은 항목이 있으면 말씀해주세요."""


REFINE_PROMPT = """사용자가 리서치 계획 수정을 요청했습니다. 반드시 수정 사항을 반영해야 합니다.

[현재 계획]
{current_plan}

[사용자 수정 요청]
{user_message}

절대 규칙:
1. 사용자의 수정 요청을 100% 반영할 것
2. 원본과 동일한 계획을 그대로 반환하지 말 것 — 반드시 변경이 있어야 함
3. 항목 추가/삭제/수정/순서변경 등 요청한 내용을 명확히 적용할 것
4. 수정된 전체 계획을 원본과 동일한 형식으로 작성할 것
5. "이 계획으로 진행할까요? 수정하고 싶은 항목이 있으면 말씀해주세요." 문구로 마무리

수정된 계획만 출력, 설명 없음."""


SIMPLE_CHAT_PROMPT = """당신은 {ticker} 종목 전문 AI 어시스턴트입니다.
FinVision에서 수집된 다음 데이터를 바탕으로 질문에 답하세요.

[보유 데이터]
{internal_context}

[이전 대화]
{history}

[현재 질문]
{question}

규칙:
- 보유 데이터에 있는 정보는 구체적 수치와 함께 답변
- 데이터에 없는 정보는 "현재 데이터에 없습니다"라고 솔직히 말할 것
- 간결하고 실용적으로 답변 (투자 결정에 도움이 되도록)
- 웹 검색 없이 보유 데이터만으로 답변"""


# ── Scout 검색 헬퍼 ────────────────────────────────────────

async def _run_search_round(queries: list[str]) -> list[str]:
    """쿼리 목록을 Parallel(배치 1회) + Tavily(병렬)로 검색 → 결과 라인 목록."""
    results: list[str] = []

    async def _parallel_batch():
        """전체 쿼리를 API 1회 호출로 처리 — 크레딧 절약."""
        if not PARALLEL_API_KEY:
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=40) as client:
                r = await client.post(
                    "https://api.parallel.ai/v1/search",
                    json={
                        "search_queries": queries,
                        "mode": "advanced",
                        "advanced_settings": {"max_results": 5},
                    },
                    headers={"x-api-key": PARALLEL_API_KEY,
                             "Content-Type": "application/json"},
                )
                if r.status_code == 200:
                    items = r.json().get("results") or r.json().get("search_results") or []
                    for item in items[:15]:
                        title = item.get("title", "")
                        content = item.get("content", item.get("excerpt", item.get("snippet", "")))[:400]
                        url = item.get("url", "")
                        domain = url.split("/")[2] if url.startswith("http") else ""
                        if title or content:
                            results.append(f"[Parallel/{domain}] {title}: {content}")
                else:
                    logger.debug(f"[scout/parallel] {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.debug(f"[scout/parallel] 배치 실패: {e}")

    async def _tavily_search(q: str):
        from app.deep_research.sources.tavily_search import _get_active_key, _mark_exhausted_and_rotate
        if not TAVILY_API_KEYS:
            return
        for _ in range(len(TAVILY_API_KEYS)):
            api_key = _get_active_key()
            if not api_key:
                break
            try:
                import httpx
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.post(
                        "https://api.tavily.com/search",
                        json={"api_key": api_key, "query": q,
                              "search_depth": "basic", "max_results": 3,
                              "include_answer": False, "include_raw_content": False},
                        headers={"Content-Type": "application/json"},
                    )
                    # 432 = Tavily 플랜 사용량 초과(라이브 실측) — 429/402와 동일 취급
                    if r.status_code in (429, 402, 432):
                        _mark_exhausted_and_rotate()
                        continue
                    if r.status_code == 200:
                        for item in r.json().get("results", [])[:3]:
                            title = item.get("title", "")
                            content = item.get("content", "")[:400]
                            url = item.get("url", "")
                            domain = url.split("/")[2] if url.startswith("http") else ""
                            if title or content:
                                results.append(f"[Tavily/{domain}] {title}: {content}")
                    break
            except Exception as e:
                logger.debug(f"[scout/tavily] {q[:40]} 실패: {e}")
                break

    async def _grounding_round():
        """라운드당 그라운딩 1콜 — 모델이 검색어 3~4개를 스스로 실행한다."""
        from app.deep_research.sources.grounding_search import grounding_source
        if not grounding_source.is_available():
            return
        try:
            rows = await grounding_source.search("; ".join(queries[:5]), max_results=10)
            for r in rows:
                from app.deep_research.common import domain_of
                results.append(f"[Grounding/{domain_of(r.url)}] {r.title}: {r.content[:400]}")
        except Exception as e:
            logger.debug(f"[scout/grounding] 실패: {e}")

    tasks = [_parallel_batch(), _grounding_round()]
    for q in queries:
        tasks.append(_tavily_search(q))
    await asyncio.gather(*tasks, return_exceptions=True)
    return results


def _dedup_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for r in lines:
        key = r[:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


async def _gen_queries(model, prompt: str, cap: int, timeout: int = 20) -> list[str]:
    """프롬프트 → 줄 단위 쿼리 목록 (실패 시 빈 목록)."""
    try:
        resp = await asyncio.to_thread(
            model.generate_content, prompt,
            request_options={"timeout": timeout},
        )
        return [ln.strip() for ln in resp.text.strip().splitlines() if ln.strip()][:cap]
    except Exception as e:
        logger.warning(f"[scout] 쿼리 생성 실패: {e}")
        return []


# 정찰 라운드 수 (1=광각 1회만, 4면 광각+추적 3회). 포화 시 조기 종료.
PLAN_SCOUT_ROUNDS = int(os.getenv("PLAN_SCOUT_ROUNDS", "4"))

# 계획 초안·심사에 쓸 추론(thinking) 모델 — 사고 깊이의 본체.
# 실측(무료티어): gemini-3.5-flash가 thinking 기본 활성(무료 가용), pro는 429.
# 쿼리 생성 등 경량 작업은 계속 flash-lite(쿼터 절약). 실패 시 lite로 폴백.
PLAN_THINKING_MODEL = os.getenv("PLAN_THINKING_MODEL", "gemini-3.5-flash")
PLAN_THINKING_BUDGET = int(os.getenv("PLAN_THINKING_BUDGET", "16384"))
_ROUND_TEXT_CAP = 3500       # 라운드당 증거 텍스트 상한
_TOTAL_EVIDENCE_CAP = 11000  # 계획 프롬프트에 넣을 전체 증거 상한


def _noop_progress(msg: str) -> None:
    pass


async def _scout_search(ticker: str, query: str, flash_model,
                        progress_cb=_noop_progress) -> str:
    """다라운드 정찰: 광각 검색 → (빈틈 분석 → 추적 검색)×N → 증거 종합.

    1라운드만으로 계획을 쓰면 표면 정보에 그친다 — 각 추적 라운드가 직전까지의
    발견에서 미검증 주장·미커버 측면·반대 관점을 파고들어 사고 깊이를 만든다.
    모델이 SATURATED를 선언하면 조기 종료(불필요한 라운드 낭비 방지).
    """
    # ── 1라운드: 광각 정찰 ──
    progress_cb("1차 정찰: 검색 쿼리 생성 중...")
    queries = await _gen_queries(
        flash_model, SCOUT_QUERIES_PROMPT.format(ticker=ticker, query=query), cap=5)
    if not queries:
        queries = [
            f"{ticker} latest news",
            f"{ticker} business overview company",
            f"{ticker} recent developments 2024 2025",
        ]
    logger.info(f"[scout] 1차(광각) 쿼리 {len(queries)}개: {queries}")
    progress_cb(f"1차 정찰: 광각 검색 실행 중 ({len(queries)}개 쿼리)...")
    round1 = _dedup_lines(await _run_search_round(queries))
    if not round1:
        return "(검색 결과 없음 — API 키 미설정 또는 검색 실패)"

    sections = [("1차 정찰 (광각)", "\n\n".join(round1)[:_ROUND_TEXT_CAP])]
    seen_keys = {r[:80] for r in round1}
    all_queries = list(queries)

    # ── 2~N라운드: 빈틈 분석 → 추적 정찰 ──
    for round_no in range(2, max(PLAN_SCOUT_ROUNDS, 1) + 1):
        progress_cb(f"{round_no}차 추적: 지금까지의 발견에서 빈틈 분석 중...")
        accumulated = "\n\n".join(text for _, text in sections)[:_TOTAL_EVIDENCE_CAP]
        followups = await _gen_queries(
            flash_model,
            SCOUT_FOLLOWUP_PROMPT.format(
                ticker=ticker, query=query, round_no=round_no - 1,
                round1_results=accumulated,
                prior_queries="\n".join(all_queries),
            ),
            cap=4, timeout=30)
        if not followups or any(q.strip().upper() == "SATURATED" for q in followups):
            logger.info(f"[scout] {round_no}차: 포화 선언 — 조기 종료")
            break
        logger.info(f"[scout] {round_no}차(추적) 쿼리 {len(followups)}개: {followups}")
        progress_cb(f"{round_no}차 추적: 검증·반대관점 검색 실행 중 ({len(followups)}개 쿼리)...")
        all_queries.extend(followups)
        rows = _dedup_lines(await _run_search_round(followups))
        rows = [r for r in rows if r[:80] not in seen_keys]
        if not rows:
            logger.info(f"[scout] {round_no}차: 신규 결과 없음 — 종료")
            break
        seen_keys.update(r[:80] for r in rows)
        sections.append((f"{round_no}차 추적 정찰 (검증·반대관점·세부)",
                         "\n\n".join(rows)[:_ROUND_TEXT_CAP]))

    combined = "\n\n".join(f"=== {name} ===\n{text}" for name, text in sections)
    logger.info(f"[scout] 정찰 완료: {len(sections)}라운드, 증거 {len(combined)}자")
    return combined[:_TOTAL_EVIDENCE_CAP]


# ── ChatService ────────────────────────────────────────────

class ChatService:
    """플랜 생성(스카우트 포함) 및 간단 채팅 서비스."""

    def __init__(self):
        self._flash_model = None

    def _get_flash(self):
        """계획 생성/채팅 — Flash 사용."""
        if self._flash_model is None and GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                self._flash_model = genai.GenerativeModel(GEMINI_FLASH_MODEL)
            except Exception as e:
                logger.error(f"[chat] Flash 초기화 실패: {e}")
        return self._flash_model

    def _get_lite(self):
        """Scout 쿼리 생성 — Lite 사용 (최저 비용)."""
        if not GEMINI_API_KEY:
            return None
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            return genai.GenerativeModel(GEMINI_LITE_MODEL)
        except Exception as e:
            logger.error(f"[chat] Lite 초기화 실패: {e}")
            return None

    def is_available(self) -> bool:
        return bool(GEMINI_API_KEY)

    async def generate_plan(
        self,
        ticker: str,
        query: str,
        internal_context: str = "",
        progress_cb=_noop_progress,
    ) -> str:
        """스카우트 검색 → 증거 기반 리서치 계획 생성."""
        model = self._get_flash()
        if not model:
            return self._fallback_plan(ticker, query)

        query_summary = query[:40] + ("..." if len(query) > 40 else "")
        has_search = bool(PARALLEL_API_KEY or TAVILY_API_KEYS)

        scout_failed = False
        if has_search:
            # Scout phase: Lite로 쿼리 생성, 실제 검색 수행
            lite = self._get_lite() or model
            try:
                scout_results = await _scout_search(ticker, query, lite,
                                                    progress_cb=progress_cb)
            except Exception as e:
                logger.warning(f"[chat] 스카우트 실패, 폴백: {e}")
                scout_results = "(스카우트 검색 실패)"
            # 정찰 전패(검색 크레딧 소진 등) → 근거 없는 계획을 꾸미지 않고
            # 내부 데이터 모드로 정직하게 강등 + 사용자에게 경고 표시
            scout_failed = scout_results.startswith("(")
            if scout_failed:
                logger.warning("[chat] 정찰 빈손 — 내부 데이터 모드로 강등")
                has_search = False

        if has_search:
            prompt = SCOUT_PLAN_PROMPT.format(
                ticker=ticker,
                query=query,
                query_summary=query_summary,
                scout_results=scout_results,
                internal_context=internal_context[:1500] if internal_context else "없음",
            )
        else:
            # 검색 API 없으면 내부 데이터만으로
            prompt = PLAN_PROMPT_NO_SCOUT.format(
                ticker=ticker,
                query=query,
                query_summary=query_summary,
                internal_context=internal_context[:2000] if internal_context else "없음",
            )

        progress_cb("정찰 증거를 종합해 계획 초안 작성 중 (추론 모델 사고)...")
        # 1차: thinking 모델(내부 사고로 증거를 실제로 따져본다) → 실패 시 lite 폴백
        from app.deep_research import llm_client
        draft = await llm_client.generate_text(
            prompt, PLAN_THINKING_MODEL,
            timeout_s=150, thinking_budget=PLAN_THINKING_BUDGET, tag="plan-draft",
        )
        if not draft:
            try:
                response = await asyncio.to_thread(
                    model.generate_content, prompt,
                    request_options={"timeout": 90},
                )
                draft = response.text.strip()
            except Exception as e:
                logger.error(f"[chat] 플랜 생성 실패: {e}")
                return self._fallback_plan(ticker, query)

        # ── 심사 패스: 초안을 증거와 대조해 결함 보강한 최종본 생성 ──
        # (검색 증거가 있을 때만 — 증거 없이 심사하면 추측만 늘린다)
        if has_search and "(검색 결과 없음" not in scout_results:
            progress_cb("심사관 패스: 초안을 증거와 대조해 결함 보강 중 (추론 모델 사고)...")
            review_prompt = PLAN_REVIEW_PROMPT.format(
                ticker=ticker, query=query,
                scout_results=scout_results[:8000], draft=draft,
            )
            reviewed = await llm_client.generate_text(
                review_prompt, PLAN_THINKING_MODEL,
                timeout_s=150, thinking_budget=PLAN_THINKING_BUDGET, tag="plan-review",
            )
            if not reviewed:
                try:
                    review = await asyncio.to_thread(
                        model.generate_content, review_prompt,
                        request_options={"timeout": 90},
                    )
                    reviewed = review.text.strip()
                except Exception as e:
                    logger.warning(f"[chat] 심사 패스 실패(초안 유지): {e}")
                    reviewed = ""
            # 심사본이 비정상적으로 짧으면(생성 실패류) 초안 유지
            if reviewed and len(reviewed) >= len(draft) * 0.6:
                logger.info(f"[chat] 심사 패스 완료: 초안 {len(draft)}자 → 최종 {len(reviewed)}자")
                return reviewed
        if scout_failed:
            draft = ("⚠️ **웹 정찰 실패** — 검색 크레딧 소진 또는 네트워크 문제로 이 계획은 "
                     "실시간 웹 근거 없이 내부 데이터·모델 지식만으로 작성됐습니다. "
                     "출처 표기가 없는 항목은 실행 단계에서 반드시 재검증됩니다.\n\n") + draft
        return draft

    async def refine_plan(self, current_plan: str, user_message: str) -> str:
        """사용자 피드백으로 계획 수정."""
        model = self._get_flash()
        if not model:
            return current_plan

        prompt = REFINE_PROMPT.format(
            current_plan=current_plan,
            user_message=user_message,
        )
        try:
            response = await asyncio.to_thread(
                model.generate_content, prompt,
                request_options={"timeout": 30},
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"[chat] 플랜 수정 실패: {e}")
            return current_plan

    async def simple_chat(
        self,
        ticker: str,
        question: str,
        internal_context: str = "",
        history: list[dict] = None,
    ) -> str:
        """간단 채팅 — Gemini만, 검색 없음."""
        model = self._get_flash()
        if not model:
            return "Gemini API 키가 설정되지 않았습니다."

        history_text = ""
        if history:
            for msg in history[-6:]:
                role = "사용자" if msg["role"] == "user" else "AI"
                history_text += f"{role}: {msg['content'][:200]}\n"

        prompt = SIMPLE_CHAT_PROMPT.format(
            ticker=ticker,
            internal_context=internal_context[:3000] if internal_context else "없음",
            history=history_text or "없음",
            question=question,
        )
        try:
            response = await asyncio.to_thread(
                model.generate_content, prompt,
                request_options={"timeout": 60},
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"[chat] 간단 채팅 실패: {e}")
            return f"응답 생성 실패: {str(e)}"

    def _fallback_plan(self, ticker: str, query: str) -> str:
        return f"""**리서치 계획: {ticker} — {query[:40]}**

1. 현황 파악: {ticker} 최신 주가 및 시장 동향 분석
2. 재무 분석: 최근 4분기 실적 및 가이던스 검토
3. 시장 반응: 어닝 서프라이즈 및 주가 반응 패턴 분석
4. 경쟁 환경: 주요 경쟁사 대비 포지셔닝
5. 리스크 요인: 주요 투자 리스크 식별
6. 종합 전망: 투자 관점 종합 의견

**예상 소요**: 약 2~5분
**활용 데이터 소스**: Parallel Search, Tavily, SEC EDGAR, FinVision 내부 데이터

이 계획으로 진행할까요?"""


# 싱글톤
chat_service = ChatService()
