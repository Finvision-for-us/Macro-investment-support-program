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


# ── 사용량 전수 집계 (유료 전환 대비 실비용 계산) ──────────────────────
# llm_client를 지나는 모든 콜(generate_structured/generate_text)의
# usage_metadata를 모델별로 누적한다. 파이프라인이 잡 시작에 reset,
# 종료에 estimated_cost로 실비용을 리포트에 싣는다.
# 한계: 레거시 SDK 직호출 폴백 경로(구조화 실패 시)는 집계 밖 — 정상 경로
# 기준의 하한값이다. 단가는 2026-07 공식 가격 페이지 실측(1M 토큰당 USD).
_PRICE_PER_M: dict[str, tuple[float, float]] = {
    # model: (input, output — 사고토큰은 출력에 포함 과금)
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
_DEFAULT_PRICE = (0.25, 1.50)  # 미등록 모델은 lite 단가로 근사

import threading as _threading

_usage_lock = _threading.Lock()
_usage: dict[str, dict[str, int]] = {}


def reset_usage() -> None:
    with _usage_lock:
        _usage.clear()


def _record_usage(model: str, resp) -> None:
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return
    inp = getattr(um, "prompt_token_count", 0) or 0
    out = getattr(um, "candidates_token_count", 0) or 0
    think = getattr(um, "thoughts_token_count", 0) or 0
    with _usage_lock:
        m = _usage.setdefault(model, {"input": 0, "output": 0, "thinking": 0, "calls": 0})
        m["input"] += inp
        m["output"] += out
        m["thinking"] += think
        m["calls"] += 1


def get_usage() -> dict[str, dict[str, int]]:
    with _usage_lock:
        return {k: dict(v) for k, v in _usage.items()}


def estimated_cost_usd() -> float:
    """누적 사용량 → USD. 사고토큰은 출력 단가로 과금(공식 정책)."""
    total = 0.0
    for model, u in get_usage().items():
        pin, pout = _PRICE_PER_M.get(model, _DEFAULT_PRICE)
        total += u["input"] * pin / 1e6 + (u["output"] + u["thinking"]) * pout / 1e6
    return total


def total_tokens() -> int:
    return sum(u["input"] + u["output"] + u["thinking"] for u in get_usage().values())


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


def _call_text_sync(model: str, prompt: str, timeout_s: int, thinking_budget: Optional[int]):
    cfg_kwargs: dict = {
        "http_options": _genai_types.HttpOptions(timeout=timeout_s * 1000),
    }
    if thinking_budget is not None:
        cfg_kwargs["thinking_config"] = _genai_types.ThinkingConfig(
            thinking_budget=thinking_budget)
    return _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=_genai_types.GenerateContentConfig(**cfg_kwargs),
    )


async def generate_text(
    prompt: str,
    model: str,
    *,
    timeout_s: int = 120,
    thinking_budget: Optional[int] = None,
    tag: str = "llm",
) -> Optional[str]:
    """일반 텍스트 생성 (thinking 지원 모델이면 사고 예산 지정 가능).

    모든 실패는 None — 호출부가 레거시/경량 경로로 폴백한다.
    실측(2026-07-19, 무료티어): gemini-3.5-flash가 thinking 기본 활성
    (사고토큰 ~1k/짧은 프롬프트), pro 계열은 429로 불가.
    """
    if not available():
        return None
    try:
        resp = await asyncio.to_thread(
            _call_text_sync, model, prompt, timeout_s, thinking_budget)
        _record_usage(model, resp)
        text = (resp.text or "").strip()
        if not text:
            return None
        um = getattr(resp, "usage_metadata", None)
        think = getattr(um, "thoughts_token_count", None) if um else None
        logger.info(f"[{tag}] {model} 텍스트 생성: 사고토큰 {think}, 출력 {_output_tokens(resp)}")
        return text
    except Exception as e:
        logger.warning(f"[{tag}] 텍스트 생성 실패({model}): {e}")
        return None


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
    ran_model = model
    try:
        resp = await asyncio.to_thread(_call_sync, model, prompt, schema, timeout_s)
    except Exception as e:
        if fallback_model and fallback_model != model and _is_quota_error(e):
            logger.warning(f"[{tag}] 구조화 호출 quota({model}) → {fallback_model} 재시도")
            try:
                ran_model = fallback_model
                resp = await asyncio.to_thread(
                    _call_sync, fallback_model, prompt, schema, timeout_s
                )
            except Exception as e2:
                logger.warning(f"[{tag}] 구조화 폴백 모델도 실패: {e2}")
                return None
        else:
            logger.warning(f"[{tag}] 구조화 호출 실패({model}): {e}")
            return None
    _record_usage(ran_model, resp)
    try:
        data = _validate(schema, resp)
    except Exception as e:
        logger.warning(f"[{tag}] 구조화 응답 검증 실패: {e}")
        return None
    return StructuredResult(data=data, output_tokens=_output_tokens(resp))
