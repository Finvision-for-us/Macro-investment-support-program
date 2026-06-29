"""깊은 분류 (Gemini) — 결정론이 못 잡는 부분 채움.

- 간접 티커(파급 종목): 공급망·경쟁·섹터 연결로 영향받는 종목 (보수적, 고신뢰만).
- long-tail 직접 티커: 대형주 사전에 없는 종목.
- event_type 정제: 'filing' 같은 generic을 구체화.
- us_market_relevant: 미국 시장과 무관하면 플래그.

LLM 호출은 비용이 있으니 filter 통과분에만, 선택적으로 돈다(DESIGN §9: AI는 늦게).
deep_classify는 llm 콜러블을 주입받아 테스트가 네트워크 없이 가능.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import get_args

from pydantic import BaseModel

from ..schema import EventType, NewsItem

_EVENT_VALUES = set(get_args(EventType))
_TAG_RE = re.compile(r"<[^>]+>")


class DeepClassification(BaseModel):
    """Gemini 구조화 출력 스키마."""

    tickers_direct: list[str] = []
    tickers_indirect: list[str] = []
    event_type: str = ""
    us_market_relevant: bool = True
    note: str = ""


_PROMPT = """You are a financial news classifier for US stock-market investors.
For the news item below return JSON:
- tickers_direct: US-listed tickers the news is PRIMARILY about.
  Use subject companies only. Uppercase symbols only.
- tickers_indirect: other US-listed tickers MATERIALLY affected via supply chain,
  competition, or sector linkage. High-confidence only, at most 5. Empty if none.
- event_type: exactly one of {events}. Use "other" if none fit.
- us_market_relevant: false only if irrelevant to US-market investors.
  Examples: lifestyle, or foreign-only with no US link.
Rules: only real US tickers, never invent. No investment advice.

Title: {title}
Summary: {summary}
Source: {source}
Already-detected direct tickers: {known}
"""


def _clean(s: str) -> str:
    return _TAG_RE.sub(" ", s or "").strip()[:1000]


def build_prompt(item: NewsItem) -> str:
    return _PROMPT.format(
        events=sorted(_EVENT_VALUES),
        title=item.title,
        summary=_clean(item.summary),
        source=item.source_name or item.source_id,
        known=item.tickers_direct,
    )


def deep_classify(item: NewsItem, llm: Callable[[str], DeepClassification]) -> DeepClassification:
    return llm(build_prompt(item))


def apply(item: NewsItem, dc: DeepClassification) -> NewsItem:
    """깊은 분류 결과를 NewsItem에 병합. 결정론 직접티커는 보존하고 추가/보강만."""
    direct = list(dict.fromkeys([*item.tickers_direct, *(t.upper() for t in dc.tickers_direct)]))
    indirect = [t.upper() for t in dc.tickers_indirect if t.upper() not in direct]
    item.tickers_direct = direct
    item.tickers_indirect = list(dict.fromkeys(indirect))

    if dc.event_type in _EVENT_VALUES:
        item.event_type = dc.event_type  # type: ignore[assignment]

    if not dc.us_market_relevant and "not_us_relevant" not in item.flags:
        item.flags.append("not_us_relevant")
    if "llm_classified" not in item.flags:
        item.flags.append("llm_classified")
    return item


def make_gemini_llm(client=None, model: str | None = None) -> Callable[[str], DeepClassification]:
    """실제 Gemini 콜러블. 구조화 출력으로 DeepClassification 반환."""
    from google.genai import types

    from ..llm import GEMINI_MODEL, gemini_client

    client = client or gemini_client()
    model = model or GEMINI_MODEL

    def llm(prompt: str) -> DeepClassification:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DeepClassification,
            ),
        )
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, DeepClassification):
            return parsed
        return DeepClassification.model_validate_json(resp.text)

    return llm


def run_deep_classify(
    news_store,
    llm: Callable[[str], DeepClassification],
    limit: int | None = None,
    only_missing_indirect: bool = True,
) -> dict:
    """filter 통과분을 깊은 분류. 비용 제어용 limit/only_missing 지원."""
    stats = {"called": 0, "with_indirect": 0, "not_relevant": 0, "errors": 0, "events": Counter()}
    for item in list(news_store.iter_items()):
        if item.filter_status != "passed":
            continue
        if only_missing_indirect and item.tickers_indirect:
            continue
        if limit is not None and stats["called"] >= limit:
            break
        try:
            dc = deep_classify(item, llm)
        except Exception:  # noqa: BLE001 — 한 건 실패가 배치를 막지 않게
            stats["errors"] += 1
            continue
        apply(item, dc)
        news_store.update(item)
        stats["called"] += 1
        if item.tickers_indirect:
            stats["with_indirect"] += 1
        if "not_us_relevant" in item.flags:
            stats["not_relevant"] += 1
        if item.event_type:
            stats["events"][item.event_type] += 1
    return stats
