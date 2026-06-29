"""§8 AI 분석층 — Story·시그널에 Gemini 정밀 영향도 스코어 부여.

입력 : CandidateResult (§7 산출물)
출력 : list[Story] — aggregated_impact·direction·confidence 재계산, 내림차순 정렬

스코어링 루브릭 (impact_score 0.0~1.0):
  0.0–0.2 : 소소한 루틴 뉴스 (소폭 실적 상회, 소규모 인사)
  0.3–0.5 : 중간 영향 (섹터 로테이션, 중소형주 M&A, 가이던스 수정)
  0.6–0.75: 유의미 (대형주 실적, 대규모 M&A, 연준 발언)
  0.8–0.9 : 시장 전체 움직임 (은행 파산, 긴급 정책, 시스템 충격)
  0.9–1.0 : 시스템 위기 수준 (리먼·팬데믹급, 극히 드묾)

모든 LLM 호출은 llm_fn으로 주입 가능 → 오프라인 테스트.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from src.causal.schema import Story
from src.ingest.schema import Event
from src.research.schema import ShallowReport

from ..candidates.pipeline import CandidateResult

Direction = Literal["positive", "negative", "uncertain"]

_SYSTEM = """\
You are a US equity market impact analyst.
Given a news event (or a causal chain of events) with supporting research, \
assess the potential market impact on US equities.

Scoring rubric for impact_score (0.0–1.0):
  0.0–0.2 : routine / minimal impact
  0.3–0.5 : moderate (sector rotation, mid-cap M&A, guidance revision)
  0.6–0.75: significant (major tech earnings, large M&A, Fed commentary)
  0.8–0.9 : major market-mover (bank failure, emergency policy)
  0.9–1.0 : systemic / rare (Lehman-scale, pandemic declaration)

direction:
  positive — net bullish for affected equities
  negative — net bearish
  uncertain — mixed or unclear

confidence: how confident you are in your assessment (0.0–1.0).

Respond in JSON only. rationale: 1-2 sentences explaining your score.\
"""

_PROMPT_TEMPLATE = """\
{header}

관련 종목: {tickers}

{events_block}
{edges_block}"""


class ImpactAnalysis(BaseModel):
    impact_score: float = Field(ge=0.0, le=1.0)
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


def _build_prompt(
    story: Story,
    events_by_id: dict[str, Event],
    shallow_reports: dict[str, ShallowReport],
    deep_reports: dict[str, dict],
) -> str:
    n = len(story.event_ids)
    header = f"[스토리 — 이벤트 {n}개 인과 체인]" if n > 1 else "[시그널 — 단일 이벤트]"
    tickers = ", ".join(story.affected_tickers[:12]) or "(없음)"

    event_parts: list[str] = []
    for i, eid in enumerate(story.event_ids, 1):
        ev = events_by_id.get(eid)
        if not ev:
            continue
        lines = [f"[이벤트 {i}]"]
        lines.append(f"제목: {ev.title}")
        if ev.summary and ev.summary != ev.title:
            lines.append(f"요약: {ev.summary[:300]}")

        sh = shallow_reports.get(eid)
        if sh and sh.background:
            lines.append(f"배경: {sh.background[:400]}")

        dr = deep_reports.get(eid)
        if dr:
            d_dir = dr.get("direction", "?")
            d_conf = dr.get("confidence", 0.0)
            lines.append(f"심층분석: 방향={d_dir} (신뢰 {d_conf:.2f})")
            causes = [
                c.get("claim", "")
                for c in dr.get("direct_causes", [])[:3]
                if c.get("claim")
            ]
            if causes:
                lines.append(f"직접 원인: {' / '.join(causes)}")
            affected = [
                c.get("claim", "")
                for c in dr.get("affected_entities", [])[:2]
                if c.get("claim")
            ]
            if affected:
                lines.append(f"영향 대상: {' / '.join(affected)}")

        event_parts.append("\n".join(lines))

    events_block = "\n\n".join(event_parts)

    edges_block = ""
    if story.edges:
        lines = ["[인과 연결]"]
        for edge in story.edges[:5]:
            lines.append(f"  → {edge.mechanism} (신뢰 {edge.confidence:.2f})")
        edges_block = "\n" + "\n".join(lines)

    return _PROMPT_TEMPLATE.format(
        header=header,
        tickers=tickers,
        events_block=events_block,
        edges_block=edges_block,
    ).strip()


def analyze_story(
    story: Story,
    events_by_id: dict[str, Event],
    shallow_reports: dict[str, ShallowReport],
    deep_reports: dict[str, dict],
    *,
    llm_fn: Callable[[str], ImpactAnalysis],
) -> Story:
    """단일 Story → AI 영향도 분석 → 갱신된 Story 반환 (불변 패턴)."""
    prompt = _build_prompt(story, events_by_id, shallow_reports, deep_reports)
    analysis = llm_fn(prompt)
    return story.model_copy(update={
        "aggregated_impact": analysis.impact_score,
        "direction": analysis.direction,
        "confidence": analysis.confidence,
    })


def score_candidates(
    result: CandidateResult,
    *,
    llm_fn: Callable[[str], ImpactAnalysis] | None = None,
    on_log=print,
) -> list[Story]:
    """CandidateResult의 모든 후보(시그널+스토리)에 AI 영향도 스코어 적용.

    반환: aggregated_impact 내림차순 정렬된 Story 목록.
    LLM 실패 시 해당 Story는 원본(prescore 기반) 유지.
    """
    if llm_fn is None:
        llm_fn = make_gemini_llm()

    total = len(result.stories)
    scored: list[Story] = []
    for i, story in enumerate(result.stories, 1):
        kind = "STORY" if len(story.event_ids) > 1 else "SIGNAL"
        on_log(f"[score {i}/{total}] {kind} {story.id[:8]}")
        try:
            updated = analyze_story(
                story,
                result.events_by_id,
                result.shallow_reports,
                result.deep_reports,
                llm_fn=llm_fn,
            )
            scored.append(updated)
        except Exception as ex:  # noqa: BLE001
            on_log(f"[score:err] {story.id[:8]} {str(ex)[:80]}")
            scored.append(story)

    scored.sort(key=lambda s: -s.aggregated_impact)
    return scored


def make_gemini_llm(client=None, model: str | None = None) -> Callable[[str], ImpactAnalysis]:
    """실 Gemini 콜러블. 구조화 출력으로 ImpactAnalysis 반환."""
    from google.genai import types

    from ..llm import GEMINI_MODEL, gemini_client

    client = client or gemini_client()
    model = model or GEMINI_MODEL

    def llm(prompt: str) -> ImpactAnalysis:
        resp = client.models.generate_content(
            model=model,
            contents=_SYSTEM + "\n\n" + prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImpactAnalysis,
            ),
        )
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, ImpactAnalysis):
            return parsed
        return ImpactAnalysis.model_validate_json(resp.text)

    return llm
