from __future__ import annotations
import asyncio
import logging
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
**활용 데이터 소스**: [검색 결과 근거가 있는 소스만 — URL 표기 금지, 도메인/유형만]
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

async def _scout_search(ticker: str, query: str, flash_model) -> str:
    """5개 쿼리 생성 → Parallel+Tavily 병렬 검색 → 결과 텍스트 반환."""
    # 1) 쿼리 생성
    try:
        q_prompt = SCOUT_QUERIES_PROMPT.format(ticker=ticker, query=query)
        resp = await asyncio.to_thread(
            flash_model.generate_content, q_prompt,
            request_options={"timeout": 20},
        )
        raw = resp.text.strip()
        queries = [line.strip() for line in raw.splitlines() if line.strip()][:5]
    except Exception as e:
        logger.warning(f"[scout] 쿼리 생성 실패: {e}")
        queries = [
            f"{ticker} latest news",
            f"{ticker} business overview company",
            f"{ticker} recent developments 2024 2025",
        ]

    logger.info(f"[scout] 검색 쿼리 {len(queries)}개: {queries}")

    # 2) Parallel(배치 1회) + Tavily(병렬) 동시 실행
    results: list[str] = []

    async def _parallel_batch():
        """5개 쿼리를 API 1회 호출로 처리 — 크레딧 절약."""
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
                    if r.status_code in (429, 402):
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

    # Parallel 1회 배치 + Tavily 5개 병렬 동시 실행
    tasks = [_parallel_batch()]
    for q in queries:
        tasks.append(_tavily_search(q))
    await asyncio.gather(*tasks, return_exceptions=True)

    if not results:
        return "(검색 결과 없음 — API 키 미설정 또는 검색 실패)"

    # 중복 제거 + 합치기 (최대 3000자)
    seen = set()
    unique = []
    for r in results:
        key = r[:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    combined = "\n\n".join(unique)
    return combined[:4000]


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
    ) -> str:
        """스카우트 검색 → 증거 기반 리서치 계획 생성."""
        model = self._get_flash()
        if not model:
            return self._fallback_plan(ticker, query)

        query_summary = query[:40] + ("..." if len(query) > 40 else "")
        has_search = bool(PARALLEL_API_KEY or TAVILY_API_KEYS)

        if has_search:
            # Scout phase: Lite로 쿼리 생성, 실제 검색 수행
            lite = self._get_lite() or model
            try:
                scout_results = await _scout_search(ticker, query, lite)
            except Exception as e:
                logger.warning(f"[chat] 스카우트 실패, 폴백: {e}")
                scout_results = "(스카우트 검색 실패)"

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

        try:
            response = await asyncio.to_thread(
                model.generate_content, prompt,
                request_options={"timeout": 45},
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"[chat] 플랜 생성 실패: {e}")
            return self._fallback_plan(ticker, query)

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
