"""M3.5 Day 5~6 ``causal.ripple`` 단위 테스트.

LLM 호출은 ``_call`` 을 monkeypatch 해 stub. 스키마 검증 / 폴백 / 잘못된 출력 처리.
"""
from __future__ import annotations

import pytest

from src.causal import ripple
from src.causal.schema import RippleEffect, Story


def _story(
    title: str = "엔비디아 GTC 키노트 후 AI 인프라 수주 확대",
    narrative_short: str = "AI 인프라 수요가 가속화되고 있습니다.",
    narrative_long: str = "본문...",
    tickers: list[str] | None = None,
    direction: str = "positive",
) -> Story:
    return Story(
        id="s1",
        event_ids=["e1"],
        title=title,
        narrative_short=narrative_short,
        narrative_long=narrative_long,
        direction=direction,  # type: ignore[arg-type]
        confidence=0.7,
        affected_tickers=tickers or ["NVDA", "AVGO"],
        aggregated_impact=0.8,
    )


# ----- 스키마 검증 -----------------------------------------------------------


def test_ripple_effect_schema_valid():
    r = RippleEffect(
        tier="direct",
        target="AVGO",
        direction="positive",
        horizon="1m",
        confidence=0.7,
        mechanism="GPU 수요 견인으로 ASIC 동반 확대",
    )
    assert r.tier == "direct"
    assert r.horizon == "1m"


def test_ripple_effect_rejects_invalid_tier():
    with pytest.raises(Exception):
        RippleEffect(
            tier="zombie",  # type: ignore[arg-type]
            target="X",
            direction="positive",
            horizon="1m",
            confidence=0.5,
            mechanism="...",
        )


def test_ripple_effect_clamps_confidence_via_validator():
    # pydantic 자체로 ge/le 강제 — 1.5는 거부
    with pytest.raises(Exception):
        RippleEffect(
            tier="direct",
            target="X",
            direction="positive",
            horizon="1m",
            confidence=1.5,
            mechanism="...",
        )


# ----- _coerce_one ---------------------------------------------------------


def test_coerce_drops_invalid_tier():
    raw = {
        "tier": "fictional",
        "target": "X",
        "direction": "positive",
        "horizon": "1m",
        "confidence": 0.5,
        "mechanism": "...",
    }
    assert ripple._coerce_one(raw) is None


def test_coerce_drops_invalid_direction():
    raw = {
        "tier": "direct",
        "target": "X",
        "direction": "bullish",  # invalid
        "horizon": "1m",
        "confidence": 0.5,
        "mechanism": "...",
    }
    assert ripple._coerce_one(raw) is None


def test_coerce_drops_invalid_horizon():
    raw = {
        "tier": "direct",
        "target": "X",
        "direction": "positive",
        "horizon": "30days",  # invalid
        "confidence": 0.5,
        "mechanism": "...",
    }
    assert ripple._coerce_one(raw) is None


def test_coerce_drops_empty_mechanism():
    raw = {
        "tier": "direct",
        "target": "X",
        "direction": "positive",
        "horizon": "1m",
        "confidence": 0.5,
        "mechanism": "",
    }
    assert ripple._coerce_one(raw) is None


def test_coerce_drops_empty_target():
    raw = {
        "tier": "direct",
        "target": "",
        "direction": "positive",
        "horizon": "1m",
        "confidence": 0.5,
        "mechanism": "...",
    }
    assert ripple._coerce_one(raw) is None


def test_coerce_clamps_confidence_above_1():
    raw = {
        "tier": "direct",
        "target": "NVDA",
        "direction": "positive",
        "horizon": "1m",
        "confidence": 1.7,
        "mechanism": "wide",
    }
    out = ripple._coerce_one(raw)
    assert out is not None
    assert out.confidence == 1.0


def test_coerce_normalizes_case():
    raw = {
        "tier": "DIRECT",
        "target": "NVDA",
        "direction": "POSITIVE",
        "horizon": "1M",
        "confidence": 0.6,
        "mechanism": "Korean mech",
    }
    out = ripple._coerce_one(raw)
    assert out is not None
    assert out.tier == "direct"
    assert out.direction == "positive"
    assert out.horizon == "1m"


# ----- generate_ripples ----------------------------------------------------


def test_generate_ripples_returns_valid_only(monkeypatch):
    fake_response = {
        "ripples": [
            {
                "tier": "direct",
                "target": "AVGO",
                "direction": "positive",
                "horizon": "1m",
                "confidence": 0.7,
                "mechanism": "GPU 수요 동반 확대.",
            },
            {  # invalid tier → drop
                "tier": "fictional",
                "target": "X",
                "direction": "positive",
                "horizon": "1m",
                "confidence": 0.5,
                "mechanism": "...",
            },
            {
                "tier": "macro",
                "target": "10년물 금리",
                "direction": "negative",
                "horizon": "1q",
                "confidence": 0.4,
                "mechanism": "AI capex 가속 → 인플레 우려",
            },
        ]
    }
    monkeypatch.setattr(ripple, "_call", lambda _: fake_response)
    out = ripple.generate_ripples(_story())
    assert len(out) == 2
    assert {r.tier for r in out} == {"direct", "macro"}


def test_generate_ripples_empty_for_storyless_input(monkeypatch):
    """title/narrative_short 비어있으면 LLM 호출 자체 안 함."""

    def boom(_):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(ripple, "_call", boom)
    out = ripple.generate_ripples(_story(title="", narrative_short=""))
    assert out == []


def test_generate_ripples_returns_empty_on_llm_error(monkeypatch):
    def fail(_):
        raise RuntimeError("gemini quota")

    monkeypatch.setattr(ripple, "_call", fail)
    assert ripple.generate_ripples(_story()) == []


def test_generate_ripples_returns_empty_for_non_list(monkeypatch):
    monkeypatch.setattr(ripple, "_call", lambda _: {"ripples": "not a list"})
    assert ripple.generate_ripples(_story()) == []


def test_generate_ripples_respects_max_items(monkeypatch):
    many = {
        "ripples": [
            {
                "tier": "direct",
                "target": f"T{i}",
                "direction": "positive",
                "horizon": "1m",
                "confidence": 0.5,
                "mechanism": f"mech {i}",
            }
            for i in range(20)
        ]
    }
    monkeypatch.setattr(ripple, "_call", lambda _: many)
    out = ripple.generate_ripples(_story(), max_items=3)
    assert len(out) == 3


def test_enrich_story_sets_ripple_effects(monkeypatch):
    monkeypatch.setattr(
        ripple,
        "_call",
        lambda _: {
            "ripples": [
                {
                    "tier": "adjacent",
                    "target": "AMD",
                    "direction": "negative",
                    "horizon": "1w",
                    "confidence": 0.3,
                    "mechanism": "경쟁심화",
                }
            ]
        },
    )
    s = _story()
    enriched = ripple.enrich_story_with_ripples(s)
    assert len(enriched.ripple_effects) == 1
    assert enriched.ripple_effects[0].target == "AMD"
    # 원본 변경 X
    assert s.ripple_effects == []


# ----- Story / LifecycleStory 통합 ------------------------------------------


def test_story_model_round_trip_with_ripples():
    s = _story()
    s_with = s.model_copy(
        update={
            "ripple_effects": [
                RippleEffect(
                    tier="direct",
                    target="AVGO",
                    direction="positive",
                    horizon="1m",
                    confidence=0.6,
                    mechanism="ASIC 수요 동반",
                )
            ]
        }
    )
    j = s_with.model_dump_json()
    assert "AVGO" in j
    assert "ripple_effects" in j


def test_lifecycle_story_inherits_ripples():
    """from_story 가 ripple_effects 를 복사하는지."""
    from src.lifecycle import store as life_store

    s = _story()
    s = s.model_copy(
        update={
            "ripple_effects": [
                RippleEffect(
                    tier="macro",
                    target="VIX",
                    direction="negative",
                    horizon="1q",
                    confidence=0.3,
                    mechanism="시장 변동성 완화",
                )
            ]
        }
    )
    ls = life_store.from_story(s, on_date="2026-05-28")
    assert len(ls.ripple_effects) == 1
    assert ls.ripple_effects[0].target == "VIX"
