from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gemini_client import (
    GeminiExecutionError,
    extract_citations,
    extract_final_answer,
    format_gemini_error,
    require_api_key,
    response_to_jsonable,
)
from schema import TraceEventItem


MODE_EXPLANATION = (
    "This mode uses Gemini generate_content with Google Search grounding. "
    "It is not the Gemini Deep Research Agent."
)


def run_grounded_research(
    *,
    instruction: str,
    user_prompt: str,
    model: str,
) -> dict[str, Any]:
    api_key = require_api_key()

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiExecutionError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client(api_key=api_key)
    request_metadata = {
        "mode": "grounded",
        "api": "generate_content",
        "model": model,
        "sdk": "google-genai",
        "uses_google_search_grounding": True,
        "google_search_grounding_requested": True,
        "instruction_chars": len(instruction),
        "prompt_chars": len(user_prompt),
        "mode_explanation": MODE_EXPLANATION,
    }
    events = [
        _event("mode_explanation", None, MODE_EXPLANATION, {"mode": "grounded"}),
    ]

    config = _build_config(types, instruction, include_grounding=True)
    try:
        response = client.models.generate_content(model=model, contents=user_prompt, config=config)
        raw = response_to_jsonable(response)
        raw["request_metadata"] = request_metadata
        return {
            "raw_response": raw,
            "final_answer": extract_final_answer(response, raw),
            "citations": extract_citations(raw),
            "request_metadata": request_metadata,
            "events": events,
            "notes": [MODE_EXPLANATION],
        }
    except Exception as first_exc:
        if not _should_retry_without_grounding(first_exc):
            raise GeminiExecutionError(format_gemini_error(first_exc)) from first_exc

        request_metadata["google_search_grounding_retry_without_tool"] = True
        request_metadata["uses_google_search_grounding"] = False
        config = _build_config(types, instruction, include_grounding=False)
        try:
            response = client.models.generate_content(model=model, contents=user_prompt, config=config)
            raw = response_to_jsonable(response)
            raw["request_metadata"] = request_metadata
            return {
                "raw_response": raw,
                "final_answer": extract_final_answer(response, raw),
                "citations": extract_citations(raw),
                "request_metadata": request_metadata,
                "events": events,
                "notes": [
                    MODE_EXPLANATION,
                    "Google Search grounding failed or was unsupported; retried without grounding tool.",
                    f"Initial grounding error: {format_gemini_error(first_exc)}",
                ],
            }
        except Exception as second_exc:
            raise GeminiExecutionError(format_gemini_error(second_exc)) from second_exc


def _build_config(types: Any, instruction: str, include_grounding: bool) -> Any:
    kwargs: dict[str, Any] = {"system_instruction": instruction}
    if include_grounding:
        try:
            kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        except Exception:
            pass
    try:
        return types.GenerateContentConfig(**kwargs)
    except Exception:
        return kwargs


def _should_retry_without_grounding(error: Exception) -> bool:
    text = str(error).lower()
    markers = ["google_search", "tool", "grounding", "unsupported", "invalid argument"]
    return any(marker in text for marker in markers)


def _event(name: str, input_value: Any, output_summary: str | None, metadata: dict[str, Any]) -> TraceEventItem:
    return TraceEventItem(
        name=name,
        input=input_value,
        output_summary=output_summary,
        metadata=metadata,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
