"""얕은 리서치: Tavily Search → Gemini Flash Lite 요약."""
from __future__ import annotations

import json
import re

from google.genai import types

from src.config import GEMINI_MODEL_FAST
from src.ingest.schema import Event
from src.llm import gemini_client, retry_gemini
from src.research.schema import ShallowReport
from src.research.tavily_client import TavilyHit
from src.research.tavily_client import search as tavily_search

SHALLOW_MAX_RESULTS = 5
SHALLOW_DAYS_WINDOW = 14

_PROMPT = """You are a financial analyst. Below is a market event and recent web search results.

EVENT
Title: {title}
Summary: {summary}
Tickers: {tickers}
Occurred at (UTC): {occurred_at}

SEARCH RESULTS (Tavily, last {days} days)
{search_block}

Based ONLY on the search results above:
- ONE sentence: background/context (what's the bigger story?)
- Direction of likely market impact on the mentioned tickers
- Confidence in your assessment (0.0 to 1.0)

LANGUAGE: Write the "background" value in natural Korean (한국어). Keep ticker symbols
(NVDA, AAPL), company names (Cerebras, OpenAI), product names (Blackwell), and numeric
values with units ($56.4B, 110x, 86%) in original form.

End your response with a JSON code block in this EXACT format:
```json
{{"background": "...", "direction": "positive|negative|uncertain", "confidence": 0.0}}
```
"""


def _format_results(hits: list[TavilyHit]) -> str:
    if not hits:
        return "(no search results)"
    parts = []
    for i, h in enumerate(hits, 1):
        snippet = h.snippet[:400] if h.snippet else "(no snippet)"
        parts.append(f"[S{i}] {h.title}\n  URL: {h.url}\n  {snippet}")
    return "\n\n".join(parts)


def _extract_json_block(text: str) -> dict | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"(\{[^{}]*\"direction\"[^{}]*\})", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _build_query(event: Event) -> str:
    """이벤트 제목 + 주요 티커 1개 정도로 쿼리 구성 (간결할수록 Tavily 결과 좋음)."""
    ticker_hint = ""
    if event.tickers_mentioned:
        ticker_hint = f" {event.tickers_mentioned[0]}"
    return f"{event.title[:120]}{ticker_hint}".strip()


@retry_gemini
def _summarize(prompt: str) -> str:
    client = gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return response.text or ""


def shallow_research(event: Event) -> ShallowReport:
    query = _build_query(event)
    hits = tavily_search(query, max_results=SHALLOW_MAX_RESULTS, days=SHALLOW_DAYS_WINDOW)

    prompt = _PROMPT.format(
        title=event.title,
        summary=event.summary[:1200],
        tickers=", ".join(event.tickers_mentioned) or "(none)",
        occurred_at=event.occurred_at.isoformat(),
        days=SHALLOW_DAYS_WINDOW,
        search_block=_format_results(hits),
    )
    text = _summarize(prompt)
    parsed = _extract_json_block(text)

    sources = [h.url for h in hits if h.url]

    if parsed is None:
        return ShallowReport(
            event_id=event.id,
            background=text.strip()[:400] or "(no response)",
            direction="uncertain",
            confidence=0.3,
            sources=sources,
        )

    direction = parsed.get("direction", "uncertain")
    if direction not in ("positive", "negative", "uncertain"):
        direction = "uncertain"

    try:
        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    return ShallowReport(
        event_id=event.id,
        background=str(parsed.get("background", ""))[:400],
        direction=direction,
        confidence=confidence,
        sources=sources,
    )
