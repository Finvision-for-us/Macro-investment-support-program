"""deep_research 공유 유틸 — 여러 모듈에 흩어져 중복되던 순수 헬퍼의 단일 소스.

여기 모인 함수들은 backend/app/deep_research 전역에서 문자 그대로 복제돼 있었다.
크로스패키지(ingest2·src)는 sys.path 경계가 달라 공유하지 않는다(의도적 분리) — 이
모듈은 backend 내부 전용이다.
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urlparse

__all__ = ["domain_of", "parse_json_object"]


def domain_of(url: Optional[str]) -> str:
    """URL → 정규화 도메인(소문자, 선행 'www.' 제거). None/빈 값도 안전.

    이전엔 ``urlparse(url).netloc.removeprefix("www.")``가 ~13곳에 흩어져 있었고
    일부만 ``.lower()``를 붙여 같은 도메인이 대소문자로 다르게 취급될 위험이 있었다.
    도메인은 RFC상 대소문자를 무시하므로 항상 소문자로 정규화한다.
    """
    if not url:
        return ""
    # 소문자화를 먼저 — 'WWW.'(대문자)는 removeprefix("www.")로 안 벗겨진다.
    return urlparse(url).netloc.lower().removeprefix("www.")


_JSON_FENCE_OPEN = re.compile(r"^```(?:json)?\n?")
_JSON_FENCE_CLOSE = re.compile(r"\n?```$")


def parse_json_object(text: str) -> Optional[dict]:
    """LLM 자유텍스트 응답에서 JSON 객체를 관용적으로 추출. 실패 시 None.

    ① 코드펜스(```json … ```) 제거 후 통째 파싱 → ② 실패하면 첫 ``{…}`` 블록만 파싱.
    planner/critic/synthesizer에 동일 복제돼 있던 ``_parse_json``의 단일 소스.
    (구조화 출력 1차 경로 실패 시 폴백용 — llm_client 참고.)
    """
    if not text:
        return None
    stripped = _JSON_FENCE_CLOSE.sub("", _JSON_FENCE_OPEN.sub("", text.strip()))
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None
