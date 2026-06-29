"""깊은 리서치: Gemini Plan → Parallel Search → Parallel Extract → Gemini Synthesize.

같은 ``session_id`` 안에서 search↔extract를 묶어 품질을 최적화한다.
"""
from __future__ import annotations

import json
import re

from google.genai import types

from src.config import GEMINI_MODEL_DEEP, GEMINI_MODEL_FAST
from src.ingest.schema import Event
from src.llm import gemini_client, retry_gemini
from src.research.parallel_client import (
    extract as parallel_extract,
)
from src.research.parallel_client import (
    new_session_id,
)
from src.research.parallel_client import (
    search as parallel_search,
)
from src.research.schema import (
    CausalNode,
    DeepReport,
    EvidenceItem,
    ShallowReport,
    SubQuestionPlan,
)

# sub-question 수: 깊이 vs 비용 트레이드오프. 4가 sweet spot
SUBQUESTION_COUNT = 4
URLS_TO_EXTRACT_PER_QUESTION = 3
PARALLEL_SEARCH_MODE = "basic"


_PLAN_PROMPT = """You are a financial analyst planning a deep investigation.

EVENT
Title: {title}
Summary: {summary}
Tickers: {tickers}
Shallow assessment: {shallow_background}
Initial direction guess: {direction}

Produce {n} sub-questions that, when answered, reveal:
1. Direct upstream causes
2. Other entities (stocks, sectors) likely affected
3. Counter-arguments or contradicting evidence
4. Specific watch-points in the next 1-4 weeks

Each sub-question should be 6-12 words, keyword-style suitable for web search.

Return ONLY a JSON object: {{"sub_questions": ["q1", "q2", "q3", "q4"]}}
"""

_SYNTHESIS_PROMPT = """You are a financial analyst writing a structured causal report.

EVENT
Title: {title}
Summary: {summary}
Tickers: {tickers}

COLLECTED EVIDENCE (each finding lists its source URLs)
{evidence_block}

AVAILABLE SOURCE URLS (cite by exact URL string, do NOT invent new URLs)
{source_registry}

STRICT RULES
1. Every claim MUST cite at least one URL from the available list.
2. Do not invent facts. If evidence is missing for a section, leave it empty.
3. A claim with exactly 1 source is allowed — UI will mark it "single-source".
4. Direction = market impact on affected stocks
   (positive=호재, negative=악재, uncertain=불확실).
5. Confidence = (source diversity) × (mechanism clarity), in [0, 1].
6. LANGUAGE: Write every "claim" field in natural Korean (한국어로 자연스럽게). Keep ticker
   symbols (NVDA, AAPL), company names (Cerebras, OpenAI), product names (Blackwell, B300),
   and numeric values with units ($56.4B, 110x, 86%) in their original form.

Return ONLY JSON in this exact schema (no prose, no code block):
{{
  "background": [{{"claim": "...", "source_urls": ["..."]}}],
  "direct_causes": [{{"claim": "...", "source_urls": ["..."]}}],
  "affected_entities": [{{"claim": "...", "source_urls": ["..."]}}],
  "counter_evidence": [{{"claim": "...", "source_urls": ["..."]}}],
  "watch_points": [{{"claim": "...", "source_urls": ["..."]}}],
  "direction": "positive|negative|uncertain",
  "confidence": 0.0
}}
"""


def _strip_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    return m.group(1) if m else text


@retry_gemini
def _plan_subquestions(event: Event, shallow: ShallowReport) -> SubQuestionPlan:
    client = gemini_client()
    prompt = _PLAN_PROMPT.format(
        title=event.title,
        summary=event.summary[:1200],
        tickers=", ".join(event.tickers_mentioned) or "(none)",
        shallow_background=shallow.background,
        direction=shallow.direction,
        n=SUBQUESTION_COUNT,
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            response_mime_type="application/json",
            response_schema=SubQuestionPlan,
        ),
    )
    if response.parsed is not None:
        return response.parsed
    return SubQuestionPlan(**json.loads(_strip_json(response.text or "{}")))


def _gather_evidence(
    event: Event,
    sub_questions: list[str],
    session_id: str,
) -> list[EvidenceItem]:
    """sub-question 전체를 한 번에 Parallel search한 뒤, 상위 URL을 extract."""
    objective = (
        f"Build a causal understanding of: {event.title}. "
        f"Focus on upstream causes, affected entities, counter-evidence, and watch-points."
    )

    # 1) batch search: 모든 sub-question을 한 번의 호출로
    search_hits = parallel_search(
        queries=sub_questions,
        objective=objective,
        session_id=session_id,
        mode=PARALLEL_SEARCH_MODE,
        max_chars_total=6000,
    )

    # 2) 상위 URL 선별 후 extract (같은 session)
    top_urls: list[str] = []
    seen = set()
    for h in search_hits[: SUBQUESTION_COUNT * URLS_TO_EXTRACT_PER_QUESTION]:
        if h.url and h.url not in seen:
            seen.add(h.url)
            top_urls.append(h.url)

    extract_hits = parallel_extract(
        urls=top_urls,
        objective=objective,
        session_id=session_id,
        max_chars_total=12000,
        search_queries=sub_questions,
    )

    # 3) sub-question별로 evidence 묶기 — search 결과를 question에 라운드로빈 매칭
    evidence: list[EvidenceItem] = []
    per_q = max(1, len(search_hits) // max(1, len(sub_questions)))
    for i, q in enumerate(sub_questions):
        slice_hits = search_hits[i * per_q : (i + 1) * per_q] or search_hits[:per_q]
        sources = [h.url for h in slice_hits if h.url]
        answer_parts: list[str] = []
        for h in slice_hits:
            if h.excerpts:
                answer_parts.append(f"[{h.title}] {h.joined_text[:600]}")
        # extract된 본문도 추가 (전체 공유)
        for eh in extract_hits[:URLS_TO_EXTRACT_PER_QUESTION]:
            if eh.url in sources and eh.excerpts:
                answer_parts.append(f"[FULL:{eh.title}] {eh.joined_text[:800]}")
        evidence.append(
            EvidenceItem(
                question=q,
                answer="\n\n".join(answer_parts) or "(no evidence)",
                sources=sources,
            )
        )
    return evidence


@retry_gemini
def _synthesize(event: Event, evidence: list[EvidenceItem]) -> DeepReport:
    client = gemini_client()
    all_sources: list[str] = []
    seen = set()
    for e in evidence:
        for u in e.sources:
            if u not in seen:
                seen.add(u)
                all_sources.append(u)

    evidence_parts = []
    for i, e in enumerate(evidence):
        sources = ", ".join(e.sources) or "(none)"
        evidence_parts.append(
            f"[Finding {i + 1}]\nQ: {e.question}\nA: {e.answer}\nSources: {sources}"
        )
    evidence_block = "\n\n".join(evidence_parts)
    source_registry = "\n".join(f"- {u}" for u in all_sources) or "(empty)"

    prompt = _SYNTHESIS_PROMPT.format(
        title=event.title,
        summary=event.summary[:1200],
        tickers=", ".join(event.tickers_mentioned) or "(none)",
        evidence_block=evidence_block[:14000],
        source_registry=source_registry,
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL_DEEP,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    raw = json.loads(_strip_json(response.text or "{}"))
    return DeepReport(
        event_id=event.id,
        background=[CausalNode(**n) for n in raw.get("background", [])],
        direct_causes=[CausalNode(**n) for n in raw.get("direct_causes", [])],
        affected_entities=[CausalNode(**n) for n in raw.get("affected_entities", [])],
        counter_evidence=[CausalNode(**n) for n in raw.get("counter_evidence", [])],
        watch_points=[CausalNode(**n) for n in raw.get("watch_points", [])],
        direction=raw.get("direction", "uncertain"),
        confidence=float(raw.get("confidence", 0.5)),
        all_sources=all_sources,
    )


def deep_research(event: Event, shallow: ShallowReport) -> tuple[DeepReport, list[EvidenceItem]]:
    print("  [stage:plan]")
    plan = _plan_subquestions(event, shallow)

    print(f"  [stage:parallel search+extract] ({len(plan.sub_questions)} sub-questions)")
    session_id = new_session_id(prefix=f"event-{event.id[:8]}")
    evidence = _gather_evidence(event, plan.sub_questions, session_id)

    print("  [stage:synthesize]")
    report = _synthesize(event, evidence)
    return report, evidence
