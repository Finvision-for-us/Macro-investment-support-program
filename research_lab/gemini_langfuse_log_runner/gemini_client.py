from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from schema import CitationItem


DEFAULT_MODEL = "deep-research-pro-preview-12-2025"
URL_RE = re.compile(r"https?://[^\s\]\)\}\>,\"']+", re.IGNORECASE)


class GeminiExecutionError(RuntimeError):
    pass


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    local_env = Path(__file__).with_name(".env")
    if local_env.exists():
        load_dotenv(local_env)
    load_dotenv()


def resolve_model(cli_model: str | None = None) -> str:
    return cli_model or os.getenv("GEMINI_DEEP_RESEARCH_MODEL") or DEFAULT_MODEL


def read_text_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def run_gemini_deep_research(
    *,
    instruction: str,
    user_prompt: str,
    model: str,
) -> dict[str, Any]:
    load_env()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiExecutionError("GEMINI_API_KEY is missing. Create .env from .env.example and add your Gemini API key.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiExecutionError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client(api_key=api_key)
    request_metadata = {
        "model": model,
        "sdk": "google-genai",
        "google_search_grounding_requested": True,
        "instruction_chars": len(instruction),
        "prompt_chars": len(user_prompt),
    }

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
            "notes": [],
        }
    except Exception as first_exc:
        if not _should_retry_without_grounding(first_exc):
            raise GeminiExecutionError(_format_gemini_error(first_exc)) from first_exc

        request_metadata["google_search_grounding_retry_without_tool"] = True
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
                "notes": [
                    "Google Search grounding failed or was unsupported; retried without grounding tool.",
                    f"Initial grounding error: {_format_gemini_error(first_exc)}",
                ],
            }
        except Exception as second_exc:
            raise GeminiExecutionError(_format_gemini_error(second_exc)) from second_exc


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


def _format_gemini_error(error: Exception) -> str:
    text = str(error).replace(os.getenv("GEMINI_API_KEY") or "", "[REDACTED]")
    return f"Gemini API call failed: {text}"


def response_to_jsonable(response: Any) -> dict[str, Any]:
    for attr in ("model_dump", "to_json_dict"):
        method = getattr(response, attr, None)
        if callable(method):
            try:
                data = method()
                return data if isinstance(data, dict) else {"response": data}
            except Exception:
                pass

    to_json = getattr(response, "to_json", None)
    if callable(to_json):
        try:
            return json.loads(to_json())
        except Exception:
            pass

    return {
        "text": getattr(response, "text", None),
        "repr": repr(response),
    }


def extract_final_answer(response: Any, raw: dict[str, Any]) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    found_text: list[str] = []
    _collect_text_parts(raw, found_text)
    return "\n\n".join(part for part in found_text if part.strip()).strip()


def _collect_text_parts(value: Any, output: list[str]) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            output.append(value["text"])
        for child in value.values():
            _collect_text_parts(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_text_parts(child, output)


def extract_citations(raw: dict[str, Any]) -> list[CitationItem]:
    citations: list[CitationItem] = []
    _collect_citations(raw, citations)

    raw_text = json.dumps(raw, ensure_ascii=False)
    for url in URL_RE.findall(raw_text):
        citations.append(CitationItem(
            title=_domain(url),
            url=url.rstrip(".,;"),
            source_type=_guess_source_type(url),
            language=_guess_language(url),
            snippet=None,
        ))

    return _dedupe_citations(citations)


def _collect_citations(value: Any, citations: list[CitationItem]) -> None:
    if isinstance(value, dict):
        web = value.get("web")
        if isinstance(web, dict):
            uri = web.get("uri") or web.get("url")
            if uri:
                citations.append(CitationItem(
                    title=web.get("title") or _domain(uri),
                    url=uri,
                    source_type=_guess_source_type(uri),
                    language=_guess_language(uri),
                    snippet=web.get("snippet"),
                ))

        uri = value.get("uri") or value.get("url")
        if uri and isinstance(uri, str):
            citations.append(CitationItem(
                title=value.get("title") or value.get("name") or _domain(uri),
                url=uri,
                source_type=value.get("source_type") or _guess_source_type(uri),
                language=value.get("language") or _guess_language(uri),
                snippet=value.get("snippet") or value.get("text"),
            ))

        for child in value.values():
            _collect_citations(child, citations)
    elif isinstance(value, list):
        for child in value:
            _collect_citations(child, citations)


def _dedupe_citations(items: list[CitationItem]) -> list[CitationItem]:
    seen: set[str] = set()
    result: list[CitationItem] = []
    for item in items:
        key = (item.url or item.title or "").lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _domain(url: str) -> str | None:
    match = re.search(r"https?://([^/]+)", url, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _guess_source_type(url: str | None) -> str | None:
    if not url:
        return None
    lower = url.lower()
    official_markers = ["sec.gov", "csrc", "sse.com", "szse", "hkex", "dart.fss", "krx", "edinet", "jpx", ".gov"]
    if any(marker in lower for marker in official_markers):
        return "official"
    if "investor" in lower or "/ir" in lower or "ir." in lower:
        return "company_ir"
    return "web"


def _guess_language(url: str | None) -> str | None:
    if not url:
        return None
    domain = _domain(url) or ""
    if domain.endswith(".cn"):
        return "zh"
    if domain.endswith(".kr"):
        return "ko"
    if domain.endswith(".jp"):
        return "ja"
    if domain.endswith(".in"):
        return "en"
    return "en"
