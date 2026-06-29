"""ingest2용 Gemini 클라이언트 (독립). 합류 시 src/llm.py와 통합 검토.

src와 동일 패턴: GEMINI_API_KEY(.env), flash-lite 모델, 30s 타임아웃.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
# 분류는 대량·저비용 → flash-lite. (src/config와 동일 기본값)
GEMINI_MODEL: str = os.environ.get("INGEST2_GEMINI_MODEL", "gemini-3.1-flash-lite")
_TIMEOUT_MS = 30_000


class MissingGeminiKeyError(RuntimeError):
    """Gemini API 키 미설정."""


def gemini_client():
    from google import genai
    from google.genai import types

    if not GEMINI_API_KEY:
        raise MissingGeminiKeyError(
            "GEMINI_API_KEY not set. https://aistudio.google.com/apikey → .env"
        )
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=_TIMEOUT_MS),
    )
