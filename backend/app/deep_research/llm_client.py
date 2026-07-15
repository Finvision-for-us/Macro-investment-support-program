"""deep_research 구조화 출력 클라이언트 (google-genai 신형 SDK).

레거시 경로(google.generativeai 자유텍스트 → 정규식 _parse_json)는 JSON 형식을
프롬프트로만 '부탁'하므로 코드펜스·필드 누락·타입 오류로 파싱이 흔들린다
(라이브 실측: 동일 성격 보고서에서 key_findings 추출이 0~4개로 변동).
이 모듈은 response_schema(Pydantic)로 형식을 API 레벨에서 강제하는 1차 경로를 제공한다.
ingest2/classify/deep.py에서 이미 운용 중인 패턴(resp.parsed → model_validate_json)의 미러.

폴백 계약 (방어적 이식 — 기존 동작 보존):
- 어떤 실패든(SDK 미설치·quota·검증 실패) None 반환 + 경고 로그.
  호출부는 기존 레거시 경로를 그대로 유지하므로 동작이 후퇴하지 않는다.
- quota(429)는 fallback_model로 1회 재시도 — 기존 planner/critic/synthesizer의
  verify 모델 폴백 관행과 동일.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, TypeAdapter

from app.deep_research.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    logger.warning("[llm] google-genai 미설치 — 구조화 출력 비활성(레거시 경로 사용)")

_client = None  # 프로세스 싱글턴 (Client는 스레드 안전, 상태 없음)


def available() -> bool:
    return _AVAILABLE and bool(GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        _client = _genai.Client(api_key=GEMINI_API_KEY)
    return _client


@dataclass
class StructuredResult:
    data: Any           # 검증된 schema 인스턴스 (BaseModel) 또는 값 (list[str] 등)
    output_tokens: int  # usage_metadata 기반 실측 (없으면 len//4 추정)


def _is_quota_error(e: Exception) -> bool:
    s = str(e).lower()
    return "quota" in s or "429" in s or "resource_exhausted" in s


def _validate(schema, resp):
    """SDK parsed 우선, 실패 시 원문 JSON 검증. BaseModel 외 타입(list[str])도 지원."""
    parsed = getattr(resp, "parsed", None)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        if isinstance(parsed, schema):
            return parsed
        return schema.model_validate_json(resp.text or "")
    ta = TypeAdapter(schema)
    if parsed is not None:
        try:
            return ta.validate_python(parsed)
        except Exception:
            pass
    return ta.validate_json(resp.text or "")


def _output_tokens(resp) -> int:
    um = getattr(resp, "usage_metadata", None)
    n = getattr(um, "candidates_token_count", None) if um is not None else None
    if isinstance(n, int) and n > 0:
        return n
    return len(resp.text or "") // 4


def _call_sync(model: str, prompt: str, schema, timeout_s: int):
    return _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=_genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            http_options=_genai_types.HttpOptions(timeout=timeout_s * 1000),
        ),
    )


async def generate_structured(
    prompt: str,
    schema,
    model: str,
    *,
    timeout_s: int = 120,
    fallback_model: Optional[str] = None,
    tag: str = "llm",
) -> Optional[StructuredResult]:
    """구조화 출력 1차 경로. 모든 실패는 None — 호출부가 레거시로 폴백한다."""
    if not available():
        return None
    try:
        resp = await asyncio.to_thread(_call_sync, model, prompt, schema, timeout_s)
    except Exception as e:
        if fallback_model and fallback_model != model and _is_quota_error(e):
            logger.warning(f"[{tag}] 구조화 호출 quota({model}) → {fallback_model} 재시도")
            try:
                resp = await asyncio.to_thread(
                    _call_sync, fallback_model, prompt, schema, timeout_s
                )
            except Exception as e2:
                logger.warning(f"[{tag}] 구조화 폴백 모델도 실패: {e2}")
                return None
        else:
            logger.warning(f"[{tag}] 구조화 호출 실패({model}): {e}")
            return None
    try:
        data = _validate(schema, resp)
    except Exception as e:
        logger.warning(f"[{tag}] 구조화 응답 검증 실패: {e}")
        return None
    return StructuredResult(data=data, output_tokens=_output_tokens(resp))
