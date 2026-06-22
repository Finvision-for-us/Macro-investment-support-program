from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
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
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise GeminiExecutionError(
            f"Prompt file is not valid UTF-8: {file_path}. "
            "Save it as UTF-8 without ANSI/CP949 mojibake and run again."
        ) from exc

    if looks_like_mojibake(text):
        raise GeminiExecutionError(
            f"Prompt file appears to contain mojibake/garbled Korean text: {file_path}. "
            "Open the file and save the original Korean text as UTF-8."
        )
    return text


def require_api_key() -> str:
    load_env()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiExecutionError("GEMINI_API_KEY is missing. Create .env from .env.example and add your Gemini API key.")
    return api_key


def format_gemini_error(error: Exception) -> str:
    text = str(error).replace(os.getenv("GEMINI_API_KEY") or "", "[REDACTED]")
    return f"Gemini API call failed: {text}"


def make_json_safe(value: Any) -> Any:
    return _make_json_safe(value, seen=set())


def _make_json_safe(value: Any, seen: set[int]) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    object_id = id(value)
    if object_id in seen:
        return "<circular-reference>"

    if isinstance(value, dict):
        seen.add(object_id)
        try:
            return {str(_make_json_safe(key, seen)): _make_json_safe(child, seen) for key, child in value.items()}
        finally:
            seen.discard(object_id)

    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(object_id)
        try:
            return [_make_json_safe(child, seen) for child in value]
        finally:
            seen.discard(object_id)

    if isinstance(value, Path):
        return str(value)

    if hasattr(value, "model_dump"):
        try:
            return _make_json_safe(value.model_dump(), seen)
        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            return _make_json_safe(value.dict(), seen)
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        seen.add(object_id)
        try:
            public_attrs = {
                key: child
                for key, child in vars(value).items()
                if not key.startswith("_")
            }
            if public_attrs:
                return _make_json_safe(public_attrs, seen)
        except Exception:
            pass
        finally:
            seen.discard(object_id)

    return str(value)


def response_to_jsonable(response: Any) -> dict[str, Any]:
    for attr in ("model_dump", "to_json_dict"):
        method = getattr(response, attr, None)
        if callable(method):
            try:
                data = make_json_safe(method())
                return data if isinstance(data, dict) else {"response": data}
            except Exception:
                pass

    to_json = getattr(response, "to_json", None)
    if callable(to_json):
        try:
            data = json.loads(to_json())
            return make_json_safe(data) if isinstance(data, dict) else {"response": make_json_safe(data)}
        except Exception:
            pass

    fallback = {
        "text": getattr(response, "text", None),
        "repr": repr(response),
    }
    return make_json_safe(fallback)


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
    raw = make_json_safe(raw)
    citations: list[CitationItem] = []
    _collect_citations(raw, citations)

    raw_text = json.dumps(raw, ensure_ascii=False, default=str)
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
            uri = _optional_str(web.get("uri") or web.get("url"))
            if uri:
                citations.append(CitationItem(
                    title=_optional_str(web.get("title")) or _domain(uri),
                    url=uri,
                    source_type=_guess_source_type(uri),
                    language=_guess_language(uri),
                    snippet=_optional_str(web.get("snippet")),
                ))

        uri = _optional_str(value.get("uri") or value.get("url"))
        if uri:
            citations.append(CitationItem(
                title=_optional_str(value.get("title")) or _optional_str(value.get("name")) or _domain(uri),
                url=uri,
                source_type=_optional_str(value.get("source_type")) or _guess_source_type(uri),
                language=_optional_str(value.get("language")) or _guess_language(uri),
                snippet=_optional_str(value.get("snippet")) or _optional_str(value.get("text")),
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


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(make_json_safe(value))


def looks_like_mojibake(text: str) -> bool:
    if "\ufffd" in text:
        return True

    suspicious_fragments = [
        "Ã", "Â", "â€", "留", "ㅺ", "醫", "媛", "湲",
        "遺", "怨", "寃", "洹", "蹂", "嫄", "쒖", "덉",
    ]
    hits = sum(1 for fragment in suspicious_fragments if fragment in text)
    return hits >= 2
