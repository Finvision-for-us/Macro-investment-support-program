"""Story 내러티브 생성: title + narrative_short + narrative_long."""
from __future__ import annotations

import json
import re

from google.genai import types

from src.causal.schema import Story
from src.config import GEMINI_MODEL_FAST
from src.ingest.schema import Event
from src.llm import gemini_client, retry_gemini

_NARRATIVE_PROMPT = """You are a financial analyst writing a Story narrative.

STORY CONTEXT
Affected tickers: {tickers}
Overall direction: {direction}
Total events: {n_events}, causal links: {n_edges}

EVENTS IN THIS STORY
{events_block}

CAUSAL LINKS
{edges_block}

DEEP RESEARCH CLAIMS (key facts already verified)
{claims_block}

TASK
Produce three outputs (Korean, 한국어):
1. title (~50자): A single sentence headline capturing the overarching theme.
2. narrative_short (~300자): Concise summary; what's happening and the main implication.
3. narrative_long (800-1500자): Full analysis including:
   - The causal chain (use ↓ or "→" to show cause→effect)
   - Affected entities and how
   - Counter-evidence or risks
   - Watch points for the next 1-4 weeks

LANGUAGE RULES
- Write all narrative in natural Korean (한국어로 자연스럽게).
- Keep tickers (NVDA, AMD), company names (Cerebras, OpenAI), products (Blackwell, B300),
  and numeric values with units ($56.4B, 110x, 86%) in original form.
- Do NOT invent facts. Use only information from EVENTS / CAUSAL LINKS / CLAIMS above.

Return ONLY JSON in this exact shape:
{{
  "title": "...",
  "narrative_short": "...",
  "narrative_long": "..."
}}
"""


def _strip_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    return m.group(1) if m else text


def _format_events_block(story: Story, events_by_id: dict[str, Event]) -> str:
    parts = []
    for i, eid in enumerate(story.event_ids, 1):
        ev = events_by_id.get(eid)
        if not ev:
            continue
        parts.append(
            f"[E{i}] {ev.title}\n"
            f"  Date: {ev.occurred_at.isoformat()}\n"
            f"  Tickers: {', '.join(ev.tickers_mentioned[:6]) or '(none)'}\n"
            f"  Summary: {ev.summary[:300]}"
        )
    return "\n\n".join(parts) or "(no events)"


def _format_edges_block(story: Story, events_by_id: dict[str, Event]) -> str:
    if not story.edges:
        return "(none — single-event story)"
    parts = []
    for i, e in enumerate(story.edges, 1):
        a = events_by_id.get(e.from_event_id)
        b = events_by_id.get(e.to_event_id)
        a_t = a.title[:60] if a else e.from_event_id
        b_t = b.title[:60] if b else e.to_event_id
        parts.append(
            f"[Edge{i}] {a_t} → {b_t}\n"
            f"  confidence={e.confidence:.2f}, direction={e.direction}\n"
            f"  mechanism: {e.mechanism[:250]}"
        )
    return "\n\n".join(parts)


def _format_claims_block(story: Story, deep_reports: dict[str, dict]) -> str:
    parts: list[str] = []
    for eid in story.event_ids:
        report = deep_reports.get(eid)
        if not report:
            continue
        for section in (
            "background",
            "direct_causes",
            "affected_entities",
            "counter_evidence",
            "watch_points",
        ):
            for c in report.get(section, []) or []:
                txt = (c or {}).get("claim", "")
                if txt:
                    parts.append(f"  - [{section}] {txt[:250]}")
    if not parts:
        return "(no deep research available for this story)"
    return "\n".join(parts[:25])  # 너무 길면 잘라서 토큰 절감


@retry_gemini
def _call(prompt: str) -> dict:
    client = gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )
    return json.loads(_strip_json(response.text or "{}"))


def generate_narrative(
    story: Story,
    events_by_id: dict[str, Event],
    deep_reports: dict[str, dict],
) -> Story:
    """Story에 title/narrative_short/narrative_long 채워서 새 Story 반환."""
    prompt = _NARRATIVE_PROMPT.format(
        tickers=", ".join(story.affected_tickers[:10]) or "(none)",
        direction=story.direction,
        n_events=len(story.event_ids),
        n_edges=len(story.edges),
        events_block=_format_events_block(story, events_by_id),
        edges_block=_format_edges_block(story, events_by_id),
        claims_block=_format_claims_block(story, deep_reports),
    )
    try:
        result = _call(prompt)
    except Exception as e:  # noqa: BLE001
        return story.model_copy(
            update={
                "title": "(narrative generation failed)",
                "narrative_short": str(e)[:200],
                "narrative_long": "",
            }
        )
    return story.model_copy(
        update={
            "title": str(result.get("title", ""))[:120],
            "narrative_short": str(result.get("narrative_short", ""))[:600],
            "narrative_long": str(result.get("narrative_long", ""))[:3000],
        }
    )
