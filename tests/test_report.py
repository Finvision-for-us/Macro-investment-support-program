"""Markdown 리포트 렌더링 단위 테스트 (Story 단위)."""
from __future__ import annotations

from src.report.markdown import _domain, _render_claim, _render_edge, render_report


def test_domain_strips_www():
    assert _domain("https://www.reuters.com/article/foo") == "reuters.com"
    assert _domain("https://bloomberg.com/x") == "bloomberg.com"


def test_render_claim_single_source_flagged():
    out = _render_claim({"claim": "Earnings beat", "source_urls": ["https://a.com/x"]})
    assert "⚠️" in out
    assert "단일출처" in out
    assert "[a.com](https://a.com/x)" in out


def test_render_claim_multi_source_no_flag():
    out = _render_claim(
        {"claim": "X happened", "source_urls": ["https://a.com/x", "https://b.com/y"]}
    )
    assert "⚠️" not in out
    assert "[a.com](https://a.com/x)" in out
    assert "[b.com](https://b.com/y)" in out


def test_render_edge_shows_titles_and_mechanism():
    events_by_id = {
        "e1": {"title": "Event A"},
        "e2": {"title": "Event B"},
    }
    edge = {
        "from_event_id": "e1",
        "to_event_id": "e2",
        "confidence": 0.8,
        "direction": "negative",
        "mechanism": "B 종목 약세 유발",
        "inferred_by": "pairwise_llm",
    }
    lines = _render_edge(edge, events_by_id)
    joined = "\n".join(lines)
    assert "Event A" in joined and "Event B" in joined
    assert "신뢰도 0.80" in joined
    assert "📉 악재" in joined
    assert "pairwise LLM" in joined
    assert "B 종목 약세 유발" in joined


def test_render_story_report_minimum():
    stories = [
        {
            "id": "s1",
            "event_ids": ["e1", "e2"],
            "title": "NVDA Q1 실적 기대와 변동성",
            "narrative_short": "다음 주 실적 발표를 앞두고 의견이 갈리고 있다.",
            "narrative_long": "이 스토리는 두 이벤트로 구성되며, 매수 신호와 매도 신호가 동시에 존재한다.",
            "direction": "uncertain",
            "confidence": 0.7,
            "affected_tickers": ["NVDA", "AMD"],
            "aggregated_impact": 1.45,
            "edges": [
                {
                    "from_event_id": "e1",
                    "to_event_id": "e2",
                    "confidence": 0.8,
                    "direction": "uncertain",
                    "mechanism": "전망 발표 → 매수 분석",
                    "inferred_by": "pairwise_llm",
                }
            ],
            "all_sources": ["https://reuters.com/a", "https://bloomberg.com/b"],
        }
    ]
    events_by_id = {
        "e1": {
            "title": "Citi $80B revenue forecast",
            "occurred_at": "2026-05-10T00:00:00+00:00",
            "tickers_mentioned": ["NVDA"],
            "spread": 4,
        },
        "e2": {
            "title": "Should you buy NVDA before May 20?",
            "occurred_at": "2026-05-12T00:00:00+00:00",
            "tickers_mentioned": ["NVDA", "AMD"],
            "spread": 7,
        },
    }
    md = render_report("NVDA", stories, events_by_id, top_n=1)
    assert "# NVDA 심층 리서치 보고서" in md
    assert "Top 1 스토리" in md
    assert "[영향력 1.45]" in md
    assert "NVDA Q1 실적 기대와 변동성" in md
    assert "❓ 불확실" in md
    assert "🔗 인과 그래프" in md
    assert "📝 상세 분석" in md
    assert "📰 구성 이벤트" in md
    assert "🌐 출처" in md
    assert "reuters.com" in md and "bloomberg.com" in md
    assert "Citi $80B revenue forecast" in md
    assert "전망 발표 → 매수 분석" in md
