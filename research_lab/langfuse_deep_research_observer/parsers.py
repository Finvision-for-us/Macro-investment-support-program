from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from schema import EvidenceScoreItem, ResearchTrace, SourceItem, ToolCallItem


URL_RE = re.compile(r"https?://[^\s\]\)\}\>,\"']+", re.IGNORECASE)
SITE_QUERY_RE = re.compile(r"\bsite:[^\s\]\)\}\>,\"']+", re.IGNORECASE)
JURISDICTION_KEYWORDS = {
    "US": [r"\bUS\b", r"\bU\.S\.\b", r"\bUnited States\b", r"\bSEC\b", r"\bNYSE\b", r"\bNASDAQ\b"],
    "CN": [r"\bCN\b", r"\bChina\b", r"\bPRC\b", r"\bCSRC\b", r"\bSSE\b", r"\bSZSE\b"],
    "KR": [r"\bKR\b", r"\bKorea\b", r"\bDART\b", r"\bKRX\b"],
    "JP": [r"\bJP\b", r"\bJapan\b", r"\bEDINET\b", r"\bJPX\b"],
    "HK": [r"\bHK\b", r"\bHong Kong\b", r"\bHKEX\b"],
}
OFFICIAL_KEYWORDS = {
    "sec": "official",
    "edgar": "official",
    "csrc": "official",
    "sse": "official",
    "szse": "official",
    "hkex": "official",
    "dart": "official",
    "krx": "official",
    "edinet": "official",
    "jpx": "official",
    "investor": "company_ir",
    "ir.": "company_ir",
}


def parse_gemini_log(path: str) -> ResearchTrace:
    text = _read_text(path)
    return _parse_text_log(text, "gemini")


def parse_openai_log(path: str) -> ResearchTrace:
    text = _read_text(path)
    data = _try_json(text)
    if data is None:
        return _parse_text_log(text, "openai")
    return _parse_json_log(data, "openai", raw_text=text)


def parse_finvision_log(path: str) -> ResearchTrace:
    text = _read_text(path)
    data = _try_json(text)
    if data is None:
        return _parse_text_log(text, "finvision")
    return _parse_json_log(data, "finvision", raw_text=text)


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _try_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_json_log(data: Any, engine_name: str, raw_text: str = "") -> ResearchTrace:
    payload = data if isinstance(data, dict) else {"items": data}
    text = raw_text or json.dumps(payload, ensure_ascii=False)

    trace = ResearchTrace(
        engine_name=str(payload.get("engine_name") or payload.get("engine") or engine_name),
        query=_as_text(payload.get("query") or payload.get("user_question") or payload.get("prompt")),
        research_plan=_as_text_list(payload.get("research_plan") or payload.get("plan")),
        generated_queries=_as_text_list(payload.get("generated_queries") or payload.get("queries") or payload.get("search_queries")),
        official_source_queries=_as_text_list(payload.get("official_source_queries")),
        searched_sources=_as_text_list(payload.get("searched_sources") or payload.get("search_targets")),
        sources_found=_parse_sources(payload.get("sources_found") or payload.get("sources") or payload.get("search_results")),
        citations=_parse_sources(payload.get("citations")),
        tool_calls=_parse_tool_calls(payload.get("tool_calls") or payload.get("tools")),
        detected_jurisdictions=_dedupe(_as_text_list(payload.get("detected_jurisdictions")) + _extract_jurisdictions(text)),
        evidence_scores=_parse_evidence_scores(payload.get("evidence_scores")),
        cross_source_consistency=_as_text_list(payload.get("cross_source_consistency") or payload.get("cross_validation")),
        unverified_gaps=_as_text_list(payload.get("unverified_gaps") or payload.get("unknowns") or payload.get("limitations")),
        final_answer=_as_text(payload.get("final_answer") or payload.get("answer") or payload.get("output")),
        notes=_as_text_list(payload.get("notes")),
    )

    urls = _extract_urls(text)
    site_queries = _extract_site_queries(text)
    generic_queries = _extract_queries_from_text(text)
    trace.generated_queries = _dedupe(trace.generated_queries + generic_queries)
    trace.official_source_queries = _dedupe(trace.official_source_queries + [q for q in trace.generated_queries if "site:" in q.lower()] + site_queries)
    trace.sources_found = _dedupe_sources(trace.sources_found + [_source_from_url(url) for url in urls])
    if not trace.citations:
        trace.citations = [source for source in trace.sources_found if source.reliability_score is not None]
    if not trace.final_answer:
        trace.final_answer = _extract_final_answer(text)
    if not trace.query:
        trace.query = _extract_query(text)
    return trace


def _parse_text_log(text: str, engine_name: str) -> ResearchTrace:
    urls = _extract_urls(text)
    generated_queries = _extract_queries_from_text(text)
    site_queries = _extract_site_queries(text)
    tool_calls = _extract_tool_calls_from_text(text)
    gaps = _extract_gaps(text)
    final_answer = _extract_final_answer(text)

    return ResearchTrace(
        engine_name=engine_name,
        query=_extract_query(text),
        research_plan=_extract_section_items(text, ["research plan", "plan", "계획"]),
        generated_queries=_dedupe(generated_queries),
        official_source_queries=_dedupe([q for q in generated_queries if "site:" in q.lower()] + site_queries),
        searched_sources=_dedupe([_domain_from_url(url) for url in urls if _domain_from_url(url)]),
        sources_found=_dedupe_sources([_source_from_url(url) for url in urls]),
        citations=_dedupe_sources([_source_from_url(url) for url in urls]),
        tool_calls=tool_calls,
        detected_jurisdictions=_extract_jurisdictions(text),
        evidence_scores=[],
        cross_source_consistency=_extract_section_items(text, ["cross-check", "consistency", "교차검증", "일치"]),
        unverified_gaps=gaps,
        final_answer=final_answer,
        notes=_extract_section_items(text, ["notes", "메모"]),
    )


def _extract_urls(text: str) -> list[str]:
    return _dedupe([url.rstrip(".,;") for url in URL_RE.findall(text)])


def _extract_site_queries(text: str) -> list[str]:
    return _dedupe([query.rstrip(".,;") for query in SITE_QUERY_RE.findall(text)])


def _extract_queries_from_text(text: str) -> list[str]:
    queries: list[str] = []
    for line in text.splitlines():
        clean = line.strip(" -\t")
        lower = clean.lower()
        if not clean:
            continue
        if any(label in lower for label in ["query:", "search:", "search query", "검색어", "검색 쿼리"]):
            _, _, value = clean.partition(":")
            queries.append(value.strip() if value else clean)
        elif "site:" in lower:
            queries.append(clean)
    return _dedupe([q for q in queries if q])


def _extract_tool_calls_from_text(text: str) -> list[ToolCallItem]:
    calls: list[ToolCallItem] = []
    for line in text.splitlines():
        clean = line.strip(" -\t")
        lower = clean.lower()
        if not clean:
            continue
        if "tool" in lower or "도구" in lower:
            name = clean.split(":", 1)[0].replace("Tool", "").replace("tool", "").strip() or "tool_call"
            value = clean.split(":", 1)[1].strip() if ":" in clean else clean
            calls.append(ToolCallItem(tool_name=name, input=value, output_summary=None))
    return calls


def _extract_jurisdictions(text: str) -> list[str]:
    found: list[str] = []
    for jurisdiction, patterns in JURISDICTION_KEYWORDS.items():
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            found.append(jurisdiction)
    return _dedupe(found)


def _extract_gaps(text: str) -> list[str]:
    gap_lines: list[str] = []
    markers = ["unverified", "unknown", "not confirmed", "uncertain", "미확인", "불확실", "확인 필요"]
    for line in text.splitlines():
        clean = line.strip(" -\t")
        if clean and any(marker in clean.lower() for marker in markers):
            gap_lines.append(clean)
    return _dedupe(gap_lines)


def _extract_final_answer(text: str) -> str:
    patterns = [
        r"(?:final answer|final|최종 답변|answer)\s*[:：]\s*(.+)",
        r"(?:conclusion|결론)\s*[:：]\s*(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def _extract_query(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip(" -\t")
        lower = clean.lower()
        if any(label in lower for label in ["user question", "question:", "query:", "사용자 질문", "질문:"]):
            return clean.split(":", 1)[1].strip() if ":" in clean else clean
    return ""


def _extract_section_items(text: str, labels: list[str]) -> list[str]:
    lines = text.splitlines()
    items: list[str] = []
    capture = False
    for line in lines:
        clean = line.strip()
        lower = clean.lower().rstrip(":")
        if any(label.lower() in lower for label in labels):
            capture = True
            value = clean.split(":", 1)[1].strip() if ":" in clean else ""
            if value:
                items.append(value)
            continue
        if capture:
            if not clean:
                break
            if re.match(r"^[A-Za-z가-힣 ]+:", clean) and not clean.startswith(("-", "*")):
                break
            items.append(clean.strip(" -*\t"))
    return _dedupe([item for item in items if item])


def _parse_sources(value: Any) -> list[SourceItem]:
    if not value:
        return []
    if isinstance(value, str):
        return [_source_from_url(url) for url in _extract_urls(value)]
    if isinstance(value, dict):
        value = [value]
    sources: list[SourceItem] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            urls = _extract_urls(item)
            sources.extend([_source_from_url(url) for url in urls] or [SourceItem(title=item)])
        elif isinstance(item, dict):
            sources.append(SourceItem(
                title=_optional_text(item.get("title") or item.get("name")),
                url=_optional_text(item.get("url") or item.get("link")),
                source_type=_optional_text(item.get("source_type") or item.get("type")),
                language=_optional_text(item.get("language") or item.get("lang")),
                reliability_score=_optional_float(item.get("reliability_score") or item.get("score")),
            ))
    return _dedupe_sources(sources)


def _parse_tool_calls(value: Any) -> list[ToolCallItem]:
    if not value:
        return []
    if isinstance(value, dict):
        value = [value]
    calls: list[ToolCallItem] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            calls.append(ToolCallItem(tool_name="tool_call", input=item, output_summary=None))
        elif isinstance(item, dict):
            calls.append(ToolCallItem(
                tool_name=str(item.get("tool_name") or item.get("name") or item.get("type") or "tool_call"),
                input=item.get("input") or item.get("args") or item.get("arguments"),
                output_summary=_optional_text(item.get("output_summary") or item.get("summary") or item.get("output")),
            ))
    return calls


def _parse_evidence_scores(value: Any) -> list[EvidenceScoreItem]:
    if not value:
        return []
    if isinstance(value, dict):
        value = [value]
    scores: list[EvidenceScoreItem] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            scores.append(EvidenceScoreItem(
                url=_optional_text(item.get("url") or item.get("source")),
                score=_optional_float(item.get("score") or item.get("reliability_score")),
                reason=_optional_text(item.get("reason") or item.get("rationale")),
            ))
    return scores


def _source_from_url(url: str) -> SourceItem:
    domain = _domain_from_url(url)
    source_type = _source_type(domain)
    return SourceItem(
        title=domain,
        url=url,
        source_type=source_type,
        language=_language_guess(domain),
        reliability_score=_reliability_guess(source_type),
    )


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"https?://([^/]+)", url, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _source_type(domain: str | None) -> str | None:
    if not domain:
        return None
    lowered = domain.lower()
    for keyword, source_type in OFFICIAL_KEYWORDS.items():
        if keyword in lowered:
            return source_type
    if lowered.endswith(".gov") or ".gov." in lowered:
        return "official"
    return "news_or_web"


def _language_guess(domain: str | None) -> str | None:
    if not domain:
        return None
    if domain.endswith(".cn"):
        return "zh"
    if domain.endswith(".kr"):
        return "ko"
    if domain.endswith(".jp"):
        return "ja"
    return "en"


def _reliability_guess(source_type: str | None) -> float | None:
    if source_type == "official":
        return 0.95
    if source_type == "company_ir":
        return 0.85
    if source_type == "news_or_web":
        return 0.6
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _optional_text(value: Any) -> str | None:
    text = _as_text(value).strip()
    return text or None


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_as_text(item).strip() for item in value if _as_text(item).strip()]
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    return [_as_text(value)]


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def _dedupe_sources(sources: list[SourceItem]) -> list[SourceItem]:
    seen: set[str] = set()
    result: list[SourceItem] = []
    for source in sources:
        key = (source.url or source.title or "").lower()
        if key and key not in seen:
            seen.add(key)
            result.append(source)
    return result
