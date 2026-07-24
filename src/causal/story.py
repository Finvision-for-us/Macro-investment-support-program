"""Story 내러티브 생성: title + narrative_short + narrative_long."""
from __future__ import annotations

import json
import re

from google.genai import types

from src.causal.schema import Story
from src.config import GEMINI_MODEL_FAST
from src.glossary import render_glossary_block
from src.ingest.schema import Event
from src.llm import gemini_client, retry_gemini
from src.prompting import clip_for_prompt

# 출력 순서 주의: 내러티브를 먼저, title 을 마지막에 생성한다.
# LLM 은 위에서 아래로(autoregressive) 쓰므로 title 을 먼저 두면 본문이 없는 상태로
# 원문을 급히 압축하다 핵심 용어가 미끄러진다(예: 침묵기간 → 보호예수). 본문을 먼저
# 확정한 뒤 그 어휘를 그대로 재사용해 title 을 뽑게 하면 제목↔본문 용어 불일치가 사라진다.
_NARRATIVE_PROMPT = """You are a financial analyst writing a Story narrative.

STORY CONTEXT
Direct tickers (explicitly named in source articles): {tickers_direct}
Indirect tickers (AI-inferred as potentially affected, not named in articles): {tickers_indirect}
Overall direction: {direction}
Total events: {n_events}, causal links: {n_edges}

EVENTS IN THIS STORY
{events_block}

CAUSAL LINKS
{edges_block}

DEEP RESEARCH CLAIMS (key facts already verified)
{claims_block}

TASK
Produce these outputs (Korean, 한국어). Generate them IN THIS ORDER — narrative FIRST, title LAST:
1. narrative_long (800-1500자): Full analysis including:
   - The causal chain (use ↓ or "→" to show cause→effect)
   - Affected entities and how
   - Counter-evidence or risks
   - Watch points for the next 1-4 weeks
2. narrative_short (~300자): Concise summary; what's happening and the main implication.
3. title (~50자): A single-sentence headline. Derive it by compressing the narrative you just
   wrote above. Reuse the exact key terms (events, concepts, entities) as they already appear in
   your narrative — do NOT paraphrase a key term into a different or more familiar word, and do
   NOT introduce any term that does not appear in your narrative.

LANGUAGE RULES
- Write all narrative in natural Korean (한국어로 자연스럽게).
- Keep tickers (NVDA, AMD), company names (Cerebras, OpenAI), products (Blackwell, B300),
  and numeric values with units ($56.4B, 110x, 86%) in original form.
- Do NOT invent facts. Use only information from EVENTS / CAUSAL LINKS / CLAIMS above.
- In title and narrative, only reference DIRECT tickers (those explicitly named in articles).
  INDIRECT tickers are secondary speculation — mention them only in narrative_long as
  "파급 가능성" with appropriate hedging, never in the title.

TERMINOLOGY (원어 → 정답 한국어. 아래 혼동어는 title·narrative 어디에도 쓰지 말 것):
{glossary_block}

Return ONLY JSON in this exact shape (note the order — title comes LAST, after the narrative):
{{
  "narrative_long": "...",
  "narrative_short": "...",
  "title": "..."
}}
"""


def _scrub_title_tickers(
    title: str,
    direct: list[str],
    indirect: list[str],
) -> str:
    """제목에서 간접 티커(AI 추론)만 제거한다.

    직접 티커(원문에 실제 언급된)는 건드리지 않는다 — 개수와 무관하게 보존.
    간접 티커가 제목에 나타나면 AI 추론이 제목 생성에 영향을 준 것이므로 제거.
    """
    to_strip: set[str] = set(indirect)

    if not to_strip:
        return title

    # (TICKER) 형식 심볼은 제거 대상에서 제외 — 이미 한국어 회사명이 앞에 있음
    paren_tickers = {
        m.group(1)
        for m in re.finditer(r"\(([A-Z]{1,5})\)", title)
    }
    to_strip -= paren_tickers

    # 긴 심볼부터 제거해 부분치환 방지
    for ticker in sorted(to_strip, key=len, reverse=True):
        # \b 대신 ASCII 전용 비인접 조건 사용:
        # Python 유니코드 모드에서 한국어 조사(와·과·등·의)가 \w로 처리되어
        # \b가 한국어 조사 앞에서 경계를 인식하지 못하는 문제를 회피.
        # 뒤따르는 콤마(,)도 같이 제거 — 쉼표로 나열된 티커 목록에서 잔류 방지.
        title = re.sub(
            rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])[,]?\s*(?:등|와|과|및|의)?\s*",
            " ",
            title,
        )

    # 후처리
    title = re.sub(r"\(\s*\)", "", title)         # 빈 괄호 ()
    title = re.sub(r"\s+·\s+", " ", title)        # 단독 중점 " · " → 공백
    title = re.sub(r"·\s*·", "·", title)          # 이중 중점
    title = re.sub(r",\s*,", ",", title)           # 이중 콤마
    title = re.sub(r"[,·]\s*$", "", title)         # 끝 구분자
    title = re.sub(r"^[,·]\s*", "", title)         # 시작 구분자
    title = re.sub(r"[: ]+,", " ", title)          # 콜론/공백 뒤 콤마 ( ": ," 패턴)
    title = re.sub(r"[ \t]{2,}", " ", title)       # 이중 공백
    title = re.sub(r"[:—\-]\s*$", "", title).strip()
    title = re.sub(r"^[:—\-]\s*", "", title).strip()
    return title or (direct[0] if direct else "(제목 없음)")


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
        direct_str = ", ".join(ev.tickers_mentioned[:6]) or "(none)"
        indirect_str = ", ".join(ev.tickers_indirect[:4]) if ev.tickers_indirect else ""
        ticker_line = f"  Direct tickers: {direct_str}"
        if indirect_str:
            ticker_line += f"\n  Indirect (AI-inferred): {indirect_str}"
        parts.append(
            f"[E{i}] {ev.title}\n"
            f"  Date: {ev.occurred_at.isoformat()}\n"
            f"{ticker_line}\n"
            f"  Summary: {clip_for_prompt(ev.summary)}"
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
    # story.affected_tickers = direct-only (adapter fix 이후)
    # indirect: 각 이벤트의 tickers_indirect 합집합 (중복 제거, 순서 유지)
    seen: set[str] = set()
    indirect_all: list[str] = []
    for eid in story.event_ids:
        ev = events_by_id.get(eid)
        if ev:
            for t in ev.tickers_indirect:
                if t not in seen and t not in set(story.affected_tickers):
                    seen.add(t)
                    indirect_all.append(t)

    prompt = _NARRATIVE_PROMPT.format(
        tickers_direct=", ".join(story.affected_tickers[:10]) or "(none)",
        tickers_indirect=", ".join(indirect_all[:6]) or "(none)",
        direction=story.direction,
        n_events=len(story.event_ids),
        n_edges=len(story.edges),
        events_block=_format_events_block(story, events_by_id),
        edges_block=_format_edges_block(story, events_by_id),
        claims_block=_format_claims_block(story, deep_reports),
        glossary_block=render_glossary_block(),
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
    raw_title = str(result.get("title", ""))[:120]
    clean_title = _scrub_title_tickers(raw_title, story.affected_tickers, indirect_all)
    return story.model_copy(
        update={
            "title": clean_title,
            "narrative_short": str(result.get("narrative_short", ""))[:600],
            "narrative_long": str(result.get("narrative_long", ""))[:3000],
        }
    )
