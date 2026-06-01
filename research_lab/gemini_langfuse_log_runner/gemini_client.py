from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from schema import CitationItem


DEFAULT_GROUNDED_MODEL = "gemini-2.5-pro"
DEFAULT_DEEP_RESEARCH_AGENT = "deep-research-preview-04-2026"
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


def resolve_grounded_model(cli_model: str | None = None) -> str:
    deprecated = os.getenv("GEMINI_DEEP_RESEARCH_MODEL")
    return cli_model or os.getenv("GEMINI_GROUNDED_MODEL") or deprecated or DEFAULT_GROUNDED_MODEL


def resolve_deep_research_agent(cli_agent: str | None = None) -> str:
    return cli_agent or os.getenv("GEMINI_DEEP_RESEARCH_AGENT") or DEFAULT_DEEP_RESEARCH_AGENT


def read_text_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def require_api_key() -> str:
    load_env()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiExecutionError("GEMINI_API_KEY is missing. Create .env from .env.example and add your Gemini API key.")
    return api_key


def format_gemini_error(error: Exception) -> str:
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
