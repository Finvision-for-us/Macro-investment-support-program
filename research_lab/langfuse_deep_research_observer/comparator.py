from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from schema import ResearchTrace, model_to_dict


WEIGHTS = {
    "jurisdiction_detection": 15,
    "query_generation": 15,
    "official_source_coverage": 20,
    "evidence_quality": 15,
    "search_behavior": 10,
    "cross_validation": 10,
    "gap_handling": 10,
    "final_answer_structure": 5,
}


def compare_traces(traces: list[ResearchTrace], output_dir: str | Path) -> dict[str, Any]:
    if not traces:
        raise ValueError("At least one ResearchTrace is required.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    scores = {trace.engine_name: _score_trace(trace) for trace in traces}
    improvements = _finvision_improvement_raw_material(traces)
    raw = {
        "score_weights": WEIGHTS,
        "scores": scores,
        "traces": [model_to_dict(trace) for trace in traces],
        "finvision_improvement_raw_material": improvements,
    }

    raw_file = output_path / "comparison_raw_material.json"
    report_file = output_path / "comparison_report.md"
    raw_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    report_file.write_text(_render_report(traces, scores, improvements), encoding="utf-8")
    return raw


def _score_trace(trace: ResearchTrace) -> dict[str, Any]:
    category_scores = {
        "jurisdiction_detection": _cap(len(trace.detected_jurisdictions) / 3) * WEIGHTS["jurisdiction_detection"],
        "query_generation": _query_generation_score(trace) * WEIGHTS["query_generation"],
        "official_source_coverage": _official_source_score(trace) * WEIGHTS["official_source_coverage"],
        "evidence_quality": _evidence_quality_score(trace) * WEIGHTS["evidence_quality"],
        "search_behavior": _search_behavior_score(trace) * WEIGHTS["search_behavior"],
        "cross_validation": _cap(len(trace.cross_source_consistency) / 3) * WEIGHTS["cross_validation"],
        "gap_handling": _gap_handling_score(trace) * WEIGHTS["gap_handling"],
        "final_answer_structure": _final_answer_score(trace) * WEIGHTS["final_answer_structure"],
    }
    rounded = {key: round(value, 2) for key, value in category_scores.items()}
    rounded["total"] = round(sum(category_scores.values()), 2)
    return rounded


def _query_generation_score(trace: ResearchTrace) -> float:
    query_count_score = _cap(len(trace.generated_queries) / 5)
    site_query_bonus = 0.2 if trace.official_source_queries else 0
    multilingual_bonus = 0.2 if _has_non_ascii_query(trace.generated_queries) else 0
    return _cap(query_count_score + site_query_bonus + multilingual_bonus)


def _official_source_score(trace: ResearchTrace) -> float:
    official_sources = [
        source for source in trace.sources_found + trace.citations
        if (source.source_type or "").lower() in {"official", "company_ir"}
    ]
    query_score = _cap(len(trace.official_source_queries) / 4)
    source_score = _cap(len(official_sources) / 5)
    return _cap((query_score + source_score) / 2)


def _evidence_quality_score(trace: ResearchTrace) -> float:
    scores = [item.score for item in trace.evidence_scores if item.score is not None]
    source_scores = [source.reliability_score for source in trace.citations if source.reliability_score is not None]
    values = scores + source_scores
    if values:
        normalized = [value / 100 if value > 1 else value for value in values]
        return _cap(mean(normalized))
    return _cap(len(trace.citations) / 5)


def _search_behavior_score(trace: ResearchTrace) -> float:
    tool_score = _cap(len(trace.tool_calls) / 4)
    searched_score = _cap(len(trace.searched_sources) / 5)
    query_score = _cap(len(trace.generated_queries) / 5)
    return _cap((tool_score + searched_score + query_score) / 3)


def _gap_handling_score(trace: ResearchTrace) -> float:
    if trace.unverified_gaps:
        return 1.0
    return 0.4 if any("uncertain" in note.lower() or "미확인" in note for note in trace.notes) else 0.0


def _final_answer_score(trace: ResearchTrace) -> float:
    answer = trace.final_answer.strip()
    if not answer:
        return 0.0
    length_score = _cap(len(answer) / 500)
    citation_bonus = 0.3 if trace.citations else 0
    gap_bonus = 0.2 if trace.unverified_gaps else 0
    return _cap(length_score + citation_bonus + gap_bonus)


def _finvision_improvement_raw_material(traces: list[ResearchTrace]) -> list[dict[str, str]]:
    finvision = next((trace for trace in traces if trace.engine_name.lower() == "finvision"), None)
    if not finvision:
        return []

    external = [trace for trace in traces if trace is not finvision]
    improvements: list[dict[str, str]] = []

    fin_domains = _domains(finvision)
    external_domains = set().union(*[_domains(trace) for trace in external]) if external else set()
    missing_domains = sorted(domain for domain in external_domains - fin_domains if _looks_official_domain(domain))
    for domain in missing_domains:
        improvements.append({
            "type": "missing_official_source",
            "description": f"External research checked {domain} but FinVision did not.",
            "suggested_fix": f"Add {domain} to official source discovery when the query context matches.",
            "priority": "high",
        })

    fin_jurisdictions = set(finvision.detected_jurisdictions)
    external_jurisdictions = set().union(*[set(trace.detected_jurisdictions) for trace in external]) if external else set()
    for jurisdiction in sorted(external_jurisdictions - fin_jurisdictions):
        improvements.append({
            "type": "missing_jurisdiction",
            "description": f"External research detected {jurisdiction} but FinVision did not.",
            "suggested_fix": f"Expand jurisdiction detector keywords and source registry coverage for {jurisdiction}.",
            "priority": "medium",
        })

    if not finvision.unverified_gaps and any(trace.unverified_gaps for trace in external):
        improvements.append({
            "type": "gap_handling",
            "description": "External research explicitly listed unverified gaps but FinVision did not.",
            "suggested_fix": "Add a required uncertainty/gap section to FinVision synthesis output.",
            "priority": "medium",
        })

    if len(finvision.official_source_queries) < max([len(trace.official_source_queries) for trace in external] or [0]):
        improvements.append({
            "type": "official_query_generation",
            "description": "FinVision generated fewer official-source queries than the external research logs.",
            "suggested_fix": "Generate more site-specific queries for regulators, exchanges, and issuer IR pages.",
            "priority": "medium",
        })

    return improvements


def _render_report(traces: list[ResearchTrace], scores: dict[str, Any], improvements: list[dict[str, str]]) -> str:
    lines = [
        "# Deep Research Comparison Report",
        "",
        "## Scores",
        "",
        "| Engine | Total | Jurisdiction | Queries | Official Sources | Evidence | Search | Cross Check | Gaps | Answer |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for engine, score in scores.items():
        lines.append(
            f"| {engine} | {score['total']} | {score['jurisdiction_detection']} | "
            f"{score['query_generation']} | {score['official_source_coverage']} | "
            f"{score['evidence_quality']} | {score['search_behavior']} | "
            f"{score['cross_validation']} | {score['gap_handling']} | {score['final_answer_structure']} |"
        )

    lines.extend(["", "## Trace Summary", ""])
    for trace in traces:
        lines.extend([
            f"### {trace.engine_name}",
            "",
            f"- Query: {trace.query or '(empty)'}",
            f"- Detected jurisdictions: {', '.join(trace.detected_jurisdictions) or '(none)'}",
            f"- Generated queries: {len(trace.generated_queries)}",
            f"- Official source queries: {len(trace.official_source_queries)}",
            f"- Sources found: {len(trace.sources_found)}",
            f"- Citations: {len(trace.citations)}",
            f"- Tool calls: {len(trace.tool_calls)}",
            f"- Unverified gaps: {len(trace.unverified_gaps)}",
            "",
        ])

    lines.extend(["## FinVision Improvement Raw Material", ""])
    if improvements:
        for item in improvements:
            lines.extend([
                f"### {item['type']}",
                "",
                f"- Description: {item['description']}",
                f"- Suggested fix: {item['suggested_fix']}",
                f"- Priority: {item['priority']}",
                "",
            ])
    else:
        lines.append("No obvious FinVision improvement raw material found from the provided logs.")

    return "\n".join(lines) + "\n"


def _domains(trace: ResearchTrace) -> set[str]:
    domains: set[str] = set()
    for source in trace.sources_found + trace.citations:
        if source.url and "://" in source.url:
            domains.add(source.url.split("://", 1)[1].split("/", 1)[0].lower())
        elif source.title and "." in source.title:
            domains.add(source.title.lower())
    return domains


def _looks_official_domain(domain: str) -> bool:
    keywords = ["sec", "edgar", "csrc", "sse", "szse", "hkex", "dart", "krx", "edinet", "jpx", ".gov", "investor", "ir."]
    return any(keyword in domain for keyword in keywords)


def _has_non_ascii_query(queries: list[str]) -> bool:
    return any(any(ord(char) > 127 for char in query) for query in queries)


def _cap(value: float) -> float:
    return max(0.0, min(1.0, value))
