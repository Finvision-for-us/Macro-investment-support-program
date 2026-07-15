"""단서 추적 반복 루프 (Discovery 엔진의 심층 축, n차).

딥리서치의 '심층'은 한 번의 검색이 아니라 **꼬리물기**다. 지금까지 발견한 내용에서
새 단서(새 기업·티커·펀드·인물, 언급된 공시·문서, 사건·날짜, 인과적 후속질문)를 뽑아
그 단서를 다시 검색·확장하는 과정을 깊이/예산 한도 내에서 반복한다.

폭발과 순환은 다음으로 제어한다:
- visited: 이미 조사한 단서 재방문 금지
- max_depth: 확장 깊이(n차) 상한
- breadth: 노드당 새 단서 상한
- max_searches: 전체 검색 예산

단서 추출은 수집된 텍스트에 '실제로 등장한' 것만 뽑도록 강제한다(지어내기 금지 — 무할루시네이션).
특정 주제/사이트에 종속되지 않는 일반 능력이다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.deep_research.config import GEMINI_API_KEY
from app.deep_research.models import SearchResult
from app.deep_research import llm_client

logger = logging.getLogger(__name__)

# 단서 추출 모델 — 무료 quota가 넉넉한 모델로 라우팅(다수 호출).
DISCOVERY_LEAD_MODEL = os.getenv("DISCOVERY_LEAD_MODEL", "gemini-3.1-flash-lite")

SearchFn = Callable[[str], Awaitable[list[SearchResult]]]
LeadExtractorFn = Callable[[str, str, set], Awaitable[list[str]]]


LEAD_PROMPT = """당신은 금융 심층 리서치의 '단서 추적' 분석가입니다.

[리서치 목표]
{goal}

[지금까지 수집한 내용]
{text}

[이미 조사한 단서 — 제외할 것]
{visited}

위 수집 내용에서 '더 깊이 파고들 가치가 있는 새 단서'를 추출하세요.

[우선순위 — 높은 것부터]
1. 공식 공시·원문 문서: SEC 8-K/10-K/10-Q/Form 4, 기업 IR, 거래소 공시, 규제기관 발표, 법원/행정 문서
2. 재무 실질 단서: 매출, 마진, EPS, guidance, backlog, capex, cash flow, debt, dilution, impairment, valuation
3. 거래·소유권 단서: 지분 매각/인수, 자산 매각, JV, 고객/공급사 계약, 주요 주주·펀드 보유, 내부자 거래
4. 사업 영향 단서: 경쟁사, 대체 공급자, 수요/가격/점유율 변화, 생산능력, 지역·제품별 노출
5. 규제·관할 단서: SEC, DOJ, FTC, ITC, BIS, CSRC, HKEX, SZSE, SSE, DART, EDINET 등 확인해야 할 관할 원문

[낮은 우선순위]
- 채용/헤드헌터/HR, SEO 글, 일반 케이스스터디, 컨설팅 홍보, 팟캐스트/행사 소개, 추상적 시장전망
- 단, 위 낮은 우선순위라도 재무 수치·공식 문서명·거래 상대방·관할 원문으로 이어지는 경우만 포함

[엄격 규칙]
- 반드시 위 수집 내용에 '실제로 등장한' 것만. 지어내기·추측 금지.
- 이미 조사한 단서와 중복 금지.
- 리서치 목표와 직접 관련된 것만.
- 단서는 "회사/티커 + 사건/문서/수치/관할" 형태를 선호하라. 예: "INDI SEC 8-K Wuxi stake sale", "indie Semiconductor Wuxi revenue impact".
- 각 단서는 바로 검색 가능한 짧은 쿼리 문자열로.
- 우선순위가 높은 단서를 배열 앞쪽에 배치하라.

JSON 배열만 출력: ["단서1", "단서2", ...]  (최대 {k}개, 없으면 [])"""


_HIGH_VALUE_LEAD_KEYWORDS = (
    "sec", "8-k", "10-k", "10-q", "form 4", "edgar", "filing", "annual report",
    "quarterly report", "earnings", "guidance", "revenue", "margin", "eps",
    "cash flow", "capex", "debt", "dilution", "impairment", "valuation",
    "stake sale", "asset sale", "divest", "divestiture", "acquisition", "merger",
    "joint venture", "jv", "supplier", "customer", "contract", "backlog",
    "insider", "ownership", "holder", "13f", "proxy", "regulation", "regulatory",
    "doj", "ftc", "itc", "bis", "csrc", "hkex", "szse", "sse", "dart", "edinet",
    "공시", "사업보고서", "분기보고서", "실적", "가이던스", "매출", "마진",
    "현금흐름", "부채", "희석", "손상", "밸류에이션", "지분", "자산 매각",
    "인수", "합병", "공급", "고객", "계약", "규제", "관할", "내부자",
)

_LOW_VALUE_LEAD_KEYWORDS = (
    "recruit", "headhunt", "hiring", "job", "career", "hr", "case study",
    "webinar", "podcast", "newsletter", "seo", "marketing", "thought leadership",
    "consulting", "agency", "course", "conference", "event", "채용", "헤드헌터",
    "구인", "인사", "케이스스터디", "웨비나", "팟캐스트", "마케팅", "컨설팅",
    "행사", "강의",
)

_GROUNDING_STOPWORDS = {
    "about", "after", "analysis", "and", "for", "from", "impact", "into", "latest",
    "news", "official", "report", "search", "the", "with", "관련", "검색", "분석",
    "영향", "최신", "확인",
}


@dataclass
class LeadFollowResult:
    sources: list[SearchResult] = field(default_factory=list)   # 중복 제거된 수집 출처
    explored: list[dict] = field(default_factory=list)          # [{lead, depth}]
    edges: list[dict] = field(default_factory=list)             # [{parent, child, depth}]
    searches: int = 0


class LeadFollower:
    """단서 추적 반복 심층 탐색기 (n차)."""

    def __init__(self):
        self._search_fn: Optional[SearchFn] = None
        self._lead_extractor: Optional[LeadExtractorFn] = None
        self._model = None

    def set_search(self, search_fn: SearchFn) -> None:
        """검색 함수 주입: async (query) -> list[SearchResult]."""
        self._search_fn = search_fn

    def set_lead_extractor(self, fn: LeadExtractorFn) -> None:
        """단서 추출 함수 주입(테스트/대체용): async (text, goal, visited) -> list[str]."""
        self._lead_extractor = fn

    async def deepen(
        self,
        seed_query: str,
        max_depth: int = 3,
        breadth: int = 3,
        max_searches: int = 30,
    ) -> LeadFollowResult:
        """seed_query에서 시작해 단서를 n차로 추적하며 심층 확장."""
        result = LeadFollowResult()
        if not self._search_fn or not seed_query.strip():
            return result

        visited: set[str] = set()
        seen_urls: set[str] = set()
        # BFS 큐: (lead, depth, parent)
        frontier: list[tuple[str, int, Optional[str]]] = [(seed_query.strip(), 0, None)]

        while frontier and result.searches < max_searches:
            lead, depth, parent = frontier.pop(0)
            key = lead.lower().strip()
            if not key or key in visited:
                continue
            visited.add(key)
            result.explored.append({"lead": lead, "depth": depth})
            if parent is not None:
                result.edges.append({"parent": parent, "child": lead, "depth": depth})

            # 이 단서 검색
            try:
                res = await self._search_fn(lead)
            except Exception as e:
                logger.warning(f"[lead] 검색 실패 '{lead[:40]}': {e}")
                res = []
            result.searches += 1
            for r in res or []:
                if r.url and r.url not in seen_urls:
                    seen_urls.add(r.url)
                    result.sources.append(r)

            # 깊이 상한 도달 시 확장 중단
            if depth >= max_depth:
                continue

            # 새 결과에서 새 단서 추출 → 다음 깊이로 enqueue
            text = _snippets(res)
            if not text:
                continue
            new_leads = await self._extract_leads(text, seed_query, visited, k=breadth)
            for nl in new_leads[:breadth]:
                if nl.lower().strip() not in visited:
                    frontier.append((nl, depth + 1, lead))

        logger.info(
            f"[lead] 심층 완료: 검색 {result.searches}회, 단서 {len(result.explored)}개, "
            f"출처 {len(result.sources)}개, 최대깊이 "
            f"{max((e['depth'] for e in result.explored), default=0)}"
        )
        return result

    async def _extract_leads(
        self, text: str, goal: str, visited: set, k: int = 3
    ) -> list[str]:
        """수집 텍스트에서 새 단서 추출. 주입된 추출기 우선, 없으면 Gemini."""
        if self._lead_extractor is not None:
            try:
                leads = await self._lead_extractor(text, goal, visited)
                return _rank_financial_leads(leads, visited, k, grounding_text=f"{goal}\n{text}")
            except Exception as e:
                logger.warning(f"[lead] 주입 추출기 실패: {e}")
                return []

        import asyncio
        prompt = LEAD_PROMPT.format(
            goal=goal,
            text=text[:8000],
            visited=", ".join(list(visited)[:40]) or "(없음)",
            k=k,
        )

        # ── 1차: 구조화 출력 (list[str] 스키마 — 배열 형식이 API 레벨에서 강제) ──
        leads: Optional[list] = None
        sres = await llm_client.generate_structured(
            prompt, list[str], DISCOVERY_LEAD_MODEL, timeout_s=60, tag="lead",
        )
        if sres is not None:
            leads = sres.data

        if leads is None:
            # ── 2차(레거시): 자유텍스트 + 정규식 파싱 — 구조화 실패 시 동작 보존 ──
            model = self._get_model()
            if model is None:
                return []
            try:
                resp = await asyncio.to_thread(model.generate_content, prompt)
                leads = _parse_json_array(resp.text or "")
            except Exception as e:
                logger.warning(f"[lead] Gemini 단서추출 실패: {e}")
                return []

        # 목표와 무관하거나 이미 방문한 것 제외
        out = [l for l in leads if isinstance(l, str) and l.strip()
               and l.lower().strip() not in visited]
        return _rank_financial_leads(out, visited, k, grounding_text=f"{goal}\n{text}")

    def _get_model(self):
        if self._model is None and GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                self._model = genai.GenerativeModel(DISCOVERY_LEAD_MODEL)
                logger.info(f"[lead] 단서추출 모델 초기화: {DISCOVERY_LEAD_MODEL}")
            except Exception as e:
                logger.error(f"[lead] 모델 초기화 실패: {e}")
        return self._model


def _snippets(results: list[SearchResult], max_chars: int = 6000) -> str:
    """검색 결과를 단서추출용 텍스트로 압축."""
    parts, remaining = [], max_chars
    for r in results or []:
        piece = f"- {r.title}: {(r.content or '')[:300]}\n"
        if remaining - len(piece) < 0:
            break
        parts.append(piece)
        remaining -= len(piece)
    return "".join(parts)


def _parse_json_array(text: str) -> list:
    text = re.sub(r'^```(?:json)?\n?', '', text.strip())
    text = re.sub(r'\n?```$', '', text)
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if m:
            try:
                v = json.loads(m.group())
                return v if isinstance(v, list) else []
            except json.JSONDecodeError:
                pass
    return []


def _rank_financial_leads(
    leads: list[str],
    visited: set,
    k: int,
    grounding_text: str = "",
) -> list[str]:
    """모델이 뽑은 단서를 금융 실질 단서 우선으로 정렬한다.

    새 단서를 만들지는 않는다. 이미 나온 문자열만 중복 제거 후 재정렬한다.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for lead in leads:
        if not isinstance(lead, str):
            continue
        item = lead.strip()
        key = item.lower()
        if not item or key in seen or key in visited:
            continue
        if grounding_text and not _is_grounded_lead(item, grounding_text):
            grounded = _strip_ungrounded_tokens(item, grounding_text)
            if not grounded or grounded.lower() in seen or grounded.lower() in visited:
                logger.info(f"[lead] 미근거 단서 제외: {item}")
                continue
            logger.info(f"[lead] 미근거 단서 축약: {item} -> {grounded}")
            item = grounded
            key = item.lower()
        seen.add(key)
        unique.append(item)

    def value_counts(item: str) -> tuple[int, int, int]:
        text = item.lower()
        high = sum(1 for kw in _HIGH_VALUE_LEAD_KEYWORDS if kw in text)
        low = sum(1 for kw in _LOW_VALUE_LEAD_KEYWORDS if kw in text)
        has_ticker_shape = 1 if re.search(r"\b[A-Z]{1,5}\b", item) else 0
        return high, low, has_ticker_shape

    def score(item: str) -> tuple[int, int]:
        high, low, has_ticker_shape = value_counts(item)
        return (high * 3 + has_ticker_shape - low * 2, -len(item))

    ranked = sorted(unique, key=score, reverse=True)
    substantive = [
        item for item in ranked
        if not (value_counts(item)[0] == 0 and value_counts(item)[1] > 0)
    ]
    return (substantive or ranked)[:k]


def _is_grounded_lead(lead: str, grounding_text: str) -> bool:
    """단서의 핵심 단어가 수집 텍스트/목표에 실제 등장하는지 확인한다."""
    corpus = grounding_text.lower()
    for token in re.findall(r"[A-Za-z가-힣][A-Za-z가-힣0-9_-]{2,}", lead):
        token_l = token.lower().strip("_-")
        if token_l in _GROUNDING_STOPWORDS:
            continue
        if re.fullmatch(r"[A-Z]{1,5}", token):
            if token_l in corpus:
                continue
            return False
        if token_l not in corpus:
            return False
    return True


def _strip_ungrounded_tokens(lead: str, grounding_text: str) -> str:
    """미근거 토큰만 삭제해 원문에 근거 있는 검색 단서로 축약한다."""
    corpus = grounding_text.lower()
    kept: list[str] = []
    for raw in lead.split():
        token = raw.strip(" ,.;:()[]{}")
        token_key = token.lower().strip("_-")
        if not token:
            continue
        if token_key in _GROUNDING_STOPWORDS or token_key in corpus:
            kept.append(token)
            continue
        if re.fullmatch(r"[A-Z]{1,5}", token) and token_key in corpus:
            kept.append(token)
    cleaned = " ".join(kept).strip()
    meaningful = [
        t for t in re.findall(r"[A-Za-z가-힣][A-Za-z가-힣0-9_-]{2,}", cleaned)
        if t.lower() not in _GROUNDING_STOPWORDS
    ]
    return cleaned if len(meaningful) >= 2 else ""


# 싱글턴 (파이프라인이 set_search로 주입)
lead_follower = LeadFollower()
