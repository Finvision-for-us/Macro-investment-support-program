"""Gemini API 공통 클라이언트/재시도 유틸."""
from __future__ import annotations

import re

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt
from tenacity.wait import wait_base

from src.config import GEMINI_API_KEY

# 응답 안 오면 30초 후 강제 종료 — edges 의 무한 hang 방지 (2026-06-22 추가)
GEMINI_TIMEOUT_MS = 30_000


class MissingGeminiKeyError(RuntimeError):
    """Gemini API 키 미설정 — retry 대상에서 제외."""


def gemini_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise MissingGeminiKeyError(
            "GEMINI_API_KEY not set. Get one at https://aistudio.google.com/apikey "
            "and add it to .env"
        )
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


class WaitFromQuotaError(wait_base):
    """429 에러에서 retryDelay를 파싱해 정확히 그만큼 대기."""

    def __call__(self, retry_state) -> float:  # noqa: D401
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is not None:
            m = re.search(r"retry in ([\d.]+)s", str(exc))
            if m:
                return float(m.group(1)) + 2.0
        return min(2 ** retry_state.attempt_number, 30)


retry_gemini = retry(stop=stop_after_attempt(5), wait=WaitFromQuotaError(), reraise=True)
