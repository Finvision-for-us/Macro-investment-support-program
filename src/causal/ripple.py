"""M3.5 Day 5~6: 스토리 단위 파급효과 생성 (PROJECT_SPEC §11.5).

narratives 단계 직후 호출 — 이미 완성된 ``Story.title/narrative_short/narrative_long``
컨텍스트를 활용해 LLM 1회 호출로 1·2·3차 파급효과 list 생성.

각 RippleEffect 는:
- **tier**: ``direct`` (직접) / ``adjacent`` (인접) / ``macro`` (거시)
- **target**: 티커, 섹터명, 또는 거시 변수
- **direction**: positive / negative / uncertain
- **horizon**: 1w / 1m / 1q
- **confidence**: 0~1
- **mechanism**: 한 문장 한국어

비용: 스토리당 LLM 1회 (~$0.001). Top 10 = ~$0.01.
실패 시 빈 list 반환 — narrative 본문은 영향 없음.
"""
from __future__ import annotations

import json
import re

from google.genai import types

from src.causal.schema import RippleEffect, Story
from src.config import GEMINI_MODEL_FAST
from src.cost_guard import log_call
from src.llm import gemini_client, retry_gemini

_RIPPLE_PROMPT = """You are a financial analyst projecting ripple effects of a story.

STORY
title: {title}
direction: {direction}
affected_tickers: {tickers}

narrative_short:
{narrative_short}

narrative_long (excerpt):
{narrative_long}

TASK
Produce up to 8 ripple effects (Korean mechanisms). Each ripple is one row:
- tier: "direct" (직접 영향: 같은 회사/섹터), "adjacent" (공급망/경쟁사/보완재), "macro" (지수/금리/원자재/환율)
- target: ticker (NVDA), sector name (반도체, 클라우드), or macro variable (10년물 금리, 달러 인덱스)
- direction: "positive" / "negative" / "uncertain"
- horizon: "1w" (1주), "1m" (1개월), "1q" (1분기)
- confidence: 0.0~1.0
- mechanism: 1 sentence Korean — 왜/어떻게 (50~120자)

RULES
- 균형 있게 — 가능하면 tier 별로 1~3개씩 (direct 만 8개 같은 편향 X)
- mechanism 은 본문 사실에 근거. 추측 시 confidence 낮춤 (~0.3).
- 모든 mechanism 한국어 자연스럽게.
- target 명확하게 (티커 또는 한국어 카테고리명).

Return ONLY JSON in this exact shape:
{{
  "ripples": [
    {{
      "tier": "direct",
      "target": "AVGO",
      "direction": "positive",
      "horizon": "1m",
      "confidence": 0.7,
      "mechanism": "..."
    }},
    ...
  ]
}}
"""


def _strip_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    return m.group(1) if m else text


@retry_gemini
def _call(prompt: str) -> dict:
    client = gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    log_call("gemini", "generate", notes="ripple effects")
    return json.loads(_strip_json(response.text or "{}"))


_VALID_TIERS = {"direct", "adjacent", "macro"}
_VALID_DIRECTIONS = {"positive", "negative", "uncertain"}
_VALID_HORIZONS = {"1w", "1m", "1q"}


def _coerce_one(raw: dict) -> RippleEffect | None:
    """LLM 출력 1건을 RippleEffect 로. 검증 실패 시 None (drop)."""
    try:
        tier = str(raw.get("tier", "")).strip().lower()
        direction = str(raw.get("direction", "")).strip().lower()
        horizon = str(raw.get("horizon", "")).strip().lower()
        target = str(raw.get("target", "")).strip()[:60]
        mechanism = str(raw.get("mechanism", "")).strip()[:300]
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        return None
    if tier not in _VALID_TIERS:
        return None
    if direction not in _VALID_DIRECTIONS:
        return None
    if horizon not in _VALID_HORIZONS:
        return None
    if not target or not mechanism:
        return None
    confidence = max(0.0, min(1.0, confidence))
    return RippleEffect(
        tier=tier,  # type: ignore[arg-type]
        target=target,
        direction=direction,  # type: ignore[arg-type]
        horizon=horizon,  # type: ignore[arg-type]
        confidence=confidence,
        mechanism=mechanism,
    )


def generate_ripples(story: Story, *, max_items: int = 8) -> list[RippleEffect]:
    """LLM 1회 호출 — 최대 ``max_items`` 개 RippleEffect.

    실패 / 빈 결과 시 빈 list. 호출자가 story.ripple_effects 에 set.
    """
    if not story.title or not story.narrative_short:
        return []
    try:
        prompt = _RIPPLE_PROMPT.format(
            title=story.title[:200],
            direction=story.direction,
            tickers=", ".join(story.affected_tickers[:10]) or "(none)",
            narrative_short=story.narrative_short[:600],
            narrative_long=story.narrative_long[:1500],
        )
        result = _call(prompt)
    except Exception:  # noqa: BLE001
        return []

    raw_list = result.get("ripples", []) or []
    if not isinstance(raw_list, list):
        return []

    out: list[RippleEffect] = []
    for r in raw_list[:max_items]:
        if isinstance(r, dict):
            coerced = _coerce_one(r)
            if coerced is not None:
                out.append(coerced)
    return out


def enrich_story_with_ripples(story: Story) -> Story:
    """편의: generate_ripples 결과를 story.model_copy 로 셋팅한 새 Story 반환."""
    ripples = generate_ripples(story)
    return story.model_copy(update={"ripple_effects": ripples})
