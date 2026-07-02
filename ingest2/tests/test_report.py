"""§10 리포트 렌더 — 오프라인(가짜 Story/Event)로 HTML·JSON 출력 검증."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from src.causal.schema import CausalEdge, Story
from src.ingest.schema import Event

from ingest2.candidates.pipeline import CandidateResult
from ingest2.rank.final import RankedStory
from ingest2.report.render import render_html, to_records, write_report


def mk_event(eid: str, title: str) -> Event:
    return Event(
        id=eid,
        title=title,
        summary=f"summary of {title}",
        occurred_at=datetime(2026, 6, 28, 12, tzinfo=UTC),
        source_urls=[f"http://x/{eid}"],
        publishers=["Reuters"],
        tickers_mentioned=["NVDA"],
        spread=1,
    )


def _result(events: list[Event], *, deep: set[str] | None = None) -> CandidateResult:
    return CandidateResult(
        stories=[],
        events_by_id={e.id: e for e in events},
        edges=[],
        shallow_reports={},
        deep_reports={eid: {} for eid in (deep or set())},
        prescore_by_id={},
        stats={"clusters_in": 5, "top_k": 5, "edges": 1, "shallow": 5, "deep": len(deep or [])},
    )


def test_to_records_story_and_signal():
    e1, e2, e3 = mk_event("e1", "Fed holds rates"), mk_event("e2", "Yields drop"), mk_event("e3", "Solo")
    edge = CausalEdge(
        from_event_id="e1", to_event_id="e2", confidence=0.8,
        direction="positive", mechanism="금리 동결 → 채권금리 하락", inferred_by="pairwise_llm",
    )
    story = Story(
        id="s1", event_ids=["e1", "e2"], title="금리 동결 스토리",
        narrative_short="연준 동결로 위험선호.", direction="positive", confidence=0.7,
        affected_tickers=["NVDA", "SPY"], aggregated_impact=0.82, edges=[edge],
        all_sources=["http://a", "http://b"],
    )
    signal = Story(
        id="s2", event_ids=["e3"], title="단일 시그널",
        direction="negative", aggregated_impact=0.4, affected_tickers=["TSLA"],
    )
    result = _result([e1, e2, e3], deep={"e1"})
    items = [RankedStory(story, 0.92, ["impact=0.82", "story_bonus=+0.08"]),
             RankedStory(signal, 0.30, ["impact=0.40"])]

    recs = to_records(items, result)
    assert recs[0]["kind"] == "STORY"
    assert recs[0]["has_deep"] is True
    assert recs[0]["n_events"] == 2
    assert recs[0]["chain"][0]["from"] == "Fed holds rates"
    assert recs[0]["chain"][0]["to"] == "Yields drop"
    assert recs[1]["kind"] == "SIGNAL"
    assert recs[1]["has_deep"] is False


def test_render_html_contains_content():
    e1 = mk_event("e1", "Fed holds rates")
    story = Story(id="s1", event_ids=["e1"], title="금리 동결",
                  affected_tickers=["NVDA"], aggregated_impact=0.5, direction="positive")
    result = _result([e1])
    html = render_html(to_records([RankedStory(story, 0.5, ["impact=0.50"])], result))
    assert "<!doctype html>" in html
    assert "금리 동결" in html
    assert "NVDA" in html
    assert "상승" in html  # direction 라벨


def test_render_html_empty():
    html = render_html([])
    assert "후보가 없습니다" in html


def test_write_report_creates_files(tmp_path):
    e1 = mk_event("e1", "Fed holds rates")
    story = Story(id="s1", event_ids=["e1"], title="금리 동결",
                  affected_tickers=["NVDA"], aggregated_impact=0.5)
    result = _result([e1])
    items = [RankedStory(story, 0.5, ["impact=0.50"])]
    paths = write_report(items, result, tmp_path, window_hours=48)
    assert paths.html.exists() and paths.json.exists()
    data = json.loads(paths.json.read_text(encoding="utf-8"))
    assert data["window_hours"] == 48
    assert data["items"][0]["title"] == "금리 동결"
    assert "<!doctype html>" in paths.html.read_text(encoding="utf-8")


def test_html_escapes_injection():
    e1 = mk_event("e1", "evt")
    story = Story(id="s1", event_ids=["e1"], title="<script>alert(1)</script>",
                  affected_tickers=[], aggregated_impact=0.3)
    result = _result([e1])
    html = render_html(to_records([RankedStory(story, 0.3, [])], result))
    assert "<script>alert(1)</script>" not in html   # 원본 태그 미노출 = XSS 안전
    assert "&lt;script&gt;" in html                   # 이스케이프된 형태로 존재
