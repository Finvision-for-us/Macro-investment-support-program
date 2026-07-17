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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from pydantic import BaseModel, Field

from src.causal.schema import Story
from src.ingest.schema import Event
from src.prompting import clip_for_prompt
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

금융 용어 주의:
- IPO quiet period / analyst coverage restrictions는 lock-up, insider share lockups,
  보호예수, 의무보유확약과 다른 개념이다. 원문이 구분하면 점수 판단에서도 구분하라.

{events_block}
{edges_block}"""


class ImpactAnalysis(BaseModel):
    impact_score: float = Field(ge=0.0, le=1.0)
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class ImpactAnalysisBatch(BaseModel):
    """여러 스토리를 LLM 1회 호출로 분석 — analyses[k]가 k번째 아이템 결과."""

    analyses: list[ImpactAnalysis] = Field(default_factory=list)


# 배치 크기: 무료티어 RPD 한도가 병목이라 호출 수를 줄이는 게 목적.
# 스토리 프롬프트가 크므로(이벤트 전문+배경+심층) 과대 배치는 컨텍스트/품질 저하 →
# 중간값 5로 균형. 배치 실패·개수 불일치 시 단건 폴백하므로 정확성은 보존된다.
DEFAULT_BATCH_SIZE = 5

_BATCH_INSTRUCTION = """\
You will receive MULTIPLE independent analysis items below, each separated by a
"===== ITEM k =====" marker. Analyze EACH item independently using the rubric.
Return JSON with an "analyses" array of EXACTLY {n} objects — one per item, in the
SAME ORDER as the items. Do not skip, merge, or reorder items.
"""


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
            lines.append(f"요약: {clip_for_prompt(ev.summary)}")

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


def _apply_analysis(story: Story, analysis: ImpactAnalysis) -> Story:
    """분석 결과를 Story에 반영(불변 패턴). 단건·배치 경로 공용."""
    return story.model_copy(update={
        "aggregated_impact": analysis.impact_score,
        "direction": analysis.direction,
        "confidence": analysis.confidence,
    })


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
    return _apply_analysis(story, llm_fn(prompt))


def _score_chunk(
    chunk: list[tuple[Story, str]],
    llm_fn: Callable[[str], ImpactAnalysis],
    batch_llm_fn: Callable[[list[str]], list[ImpactAnalysis]],
    on_log,
) -> list[Story]:
    """한 배치를 LLM 1회로 스코어. 실패·개수 불일치 시 단건(llm_fn) 폴백.

    반환 순서는 입력 chunk 순서와 동일(불변). 단건 폴백에서도 실패하면 원본 유지.
    """
    stories = [s for s, _ in chunk]
    prompts = [p for _, p in chunk]
    try:
        analyses = batch_llm_fn(prompts)
        if len(analyses) != len(prompts):
            raise ValueError(f"배치 개수 불일치: got {len(analyses)}, want {len(prompts)}")
        return [_apply_analysis(s, a) for s, a in zip(stories, analyses)]
    except Exception as ex:  # noqa: BLE001
        on_log(f"[score:batch-err] 단건 폴백 ({str(ex)[:60]})")
        out: list[Story] = []
        for s, p in zip(stories, prompts):
            try:
                out.append(_apply_analysis(s, llm_fn(p)))
            except Exception as ex2:  # noqa: BLE001
                on_log(f"[score:err] {s.id[:8]} {str(ex2)[:60]}")
                out.append(s)
        return out


def score_candidates(
    result: CandidateResult,
    *,
    llm_fn: Callable[[str], ImpactAnalysis] | None = None,
    batch_llm_fn: Callable[[list[str]], list[ImpactAnalysis]] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_log=print,
) -> list[Story]:
    """CandidateResult의 모든 후보(시그널+스토리)에 AI 영향도 스코어 적용.

    반환: aggregated_impact 내림차순 정렬된 Story 목록.
    LLM 실패 시 해당 Story는 원본(prescore 기반) 유지.

    batch_llm_fn을 주면 스토리를 batch_size개씩 묶어 LLM 1회로 스코어(무료티어 RPD
    절감). 주지 않으면 기존 동작(스토리별 8워커 병렬) 그대로 — 하위호환.
    """
    if llm_fn is None:
        llm_fn = make_gemini_llm()

    stories = result.stories
    if not stories:
        return []

    # ── 배치 경로 (opt-in): 프롬프트를 미리 만들고 batch_size로 청크 ──
    if batch_llm_fn is not None:
        pairs = [
            (
                s,
                _build_prompt(
                    s, result.events_by_id, result.shallow_reports, result.deep_reports
                ),
            )
            for s in stories
        ]
        size = max(1, batch_size)
        chunks = [pairs[i:i + size] for i in range(0, len(pairs), size)]
        on_log(f"[score] 배치 {len(chunks)}개 (스토리 {len(stories)} / 배치크기 {size})")
        # 배치 간에는 소폭 병렬(각 호출이 크므로 워커 축소). 순서 보존(pool.map).
        with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as pool:
            chunk_results = pool.map(
                lambda c: _score_chunk(c, llm_fn, batch_llm_fn, on_log), chunks
            )
        scored = [s for chunk in chunk_results for s in chunk]
        scored.sort(key=lambda s: -s.aggregated_impact)
        return scored

    # ── 기존 경로: 스토리별 8워커 병렬 (batch_llm_fn 없을 때) ──
    total = len(stories)

    def _score_one(args: tuple) -> Story:
        i, story = args
        kind = "STORY" if len(story.event_ids) > 1 else "SIGNAL"
        on_log(f"[score {i}/{total}] {kind} {story.id[:8]}")
        try:
            return analyze_story(
                story,
                result.events_by_id,
                result.shallow_reports,
                result.deep_reports,
                llm_fn=llm_fn,
            )
        except Exception as ex:  # noqa: BLE001
            on_log(f"[score:err] {story.id[:8]} {str(ex)[:80]}")
            return story

    with ThreadPoolExecutor(max_workers=8) as pool:
        scored = list(pool.map(_score_one, enumerate(stories, 1)))

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


def _build_batch_prompt(prompts: list[str]) -> str:
    """여러 스토리 프롬프트를 번호 매긴 단일 배치 프롬프트로 결합."""
    blocks = [f"===== ITEM {i} =====\n{p}" for i, p in enumerate(prompts, 1)]
    return _BATCH_INSTRUCTION.format(n=len(prompts)) + "\n" + "\n\n".join(blocks)


def make_gemini_batch_llm(
    client=None, model: str | None = None
) -> Callable[[list[str]], list[ImpactAnalysis]]:
    """실 Gemini 배치 콜러블. 여러 프롬프트 → LLM 1회 → list[ImpactAnalysis].

    구조화 출력(ImpactAnalysisBatch)으로 개수·형식을 API 레벨에서 강제한다.
    개수 불일치 검증·단건 폴백은 호출부(_score_chunk)가 담당한다.
    """
    from google.genai import types

    from ..llm import GEMINI_MODEL, gemini_client

    client = client or gemini_client()
    model = model or GEMINI_MODEL

    def batch_llm(prompts: list[str]) -> list[ImpactAnalysis]:
        if not prompts:
            return []
        resp = client.models.generate_content(
            model=model,
            contents=_SYSTEM + "\n\n" + _build_batch_prompt(prompts),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImpactAnalysisBatch,
            ),
        )
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, ImpactAnalysisBatch):
            return parsed.analyses
        return ImpactAnalysisBatch.model_validate_json(resp.text).analyses

    return batch_llm
