"""RSS 이벤트 유형 키워드 규칙 (보수적). 확신 없으면 None 반환.

구체적 규칙을 먼저 둔다(특정 → 일반 순). 깊은/애매한 분류는 향후 Gemini로.
"""
from __future__ import annotations

# (event_type, 키워드들) — 위에서부터 먼저 매칭
_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "m_and_a",
        ("to acquire", "acquisition of", "merger", "takeover", "buyout", "agrees to buy"),
    ),
    ("ipo", ("initial public offering", "files for ipo", "goes public", " ipo")),
    (
        "guidance_up",
        (
            "raises guidance",
            "raises forecast",
            "lifts outlook",
            "boosts forecast",
            "raises outlook",
        ),
    ),
    (
        "guidance_down",
        (
            "cuts guidance",
            "lowers guidance",
            "cuts forecast",
            "lowers outlook",
            "profit warning",
        ),
    ),
    (
        "ceo_change",
        (
            "new ceo",
            "ceo steps down",
            "ceo resigns",
            "names ceo",
            "appoints ceo",
            "chief executive steps down",
        ),
    ),
    ("litigation", ("lawsuit", " sues ", "settlement", "antitrust suit")),
    ("earnings", ("earnings", "quarterly results", "beats estimates", "misses estimates")),
    (
        "regulation",
        ("federal reserve", "rate hike", "rate cut", "regulator", "sec charges", "tariff"),
    ),
]


def event_for_text(text: str) -> str | None:
    t = f" {text.lower()} "
    for event, kws in _RULES:
        if any(kw in t for kw in kws):
            return event
    return None
