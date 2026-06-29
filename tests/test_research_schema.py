"""리서치 스키마 단위 테스트 (LLM 호출 없음)."""
from __future__ import annotations

from src.research.schema import CausalNode, DeepReport, ShallowReport


def test_causal_node_single_source_flag():
    assert CausalNode(claim="x", source_urls=["u1"]).is_single_source is True
    assert CausalNode(claim="x", source_urls=["u1", "u2"]).is_single_source is False
    assert CausalNode(claim="x", source_urls=[]).is_single_source is False


def test_shallow_report_roundtrip():
    r = ShallowReport(
        event_id="e1",
        background="ctx",
        direction="positive",
        confidence=0.7,
        sources=["http://a"],
    )
    again = ShallowReport(**r.model_dump(mode="json"))
    assert again == r


def test_deep_report_with_claims():
    r = DeepReport(
        event_id="e1",
        background=[CausalNode(claim="bg", source_urls=["u1"])],
        direct_causes=[CausalNode(claim="cause", source_urls=["u1", "u2"])],
        direction="negative",
        confidence=0.6,
        all_sources=["u1", "u2"],
    )
    assert len(r.background) == 1
    assert r.direct_causes[0].is_single_source is False
    assert r.background[0].is_single_source is True
