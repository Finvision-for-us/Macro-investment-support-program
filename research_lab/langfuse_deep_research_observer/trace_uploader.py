from __future__ import annotations

from typing import Any

from langfuse_client import get_langfuse_client
from schema import ResearchTrace, model_to_dict


def upload_research_trace(trace: ResearchTrace, client: Any | None = None) -> str | None:
    langfuse = client or get_langfuse_client(required=True)
    if hasattr(langfuse, "start_as_current_observation"):
        return _upload_modern_trace(langfuse, trace)

    return _upload_legacy_trace(langfuse, trace)


def _upload_legacy_trace(langfuse: Any, trace: ResearchTrace) -> str | None:
    trace_obj = langfuse.trace(
        name=f"deep_research_comparison::{trace.engine_name}",
        input={"query": trace.query},
        output={"final_answer": trace.final_answer},
        metadata={
            "engine_name": trace.engine_name,
            "query": trace.query,
            "detected_jurisdictions": trace.detected_jurisdictions,
        },
    )

    _span(trace_obj, "research_plan", {"query": trace.query}, {"plan": trace.research_plan})
    _span(trace_obj, "query_generation", {"query": trace.query}, {"generated_queries": trace.generated_queries})
    _span(
        trace_obj,
        "official_source_search",
        {"official_source_queries": trace.official_source_queries},
        {"searched_sources": trace.searched_sources, "sources_found": [model_to_dict(source) for source in trace.sources_found]},
    )
    _span(
        trace_obj,
        "tool_calls",
        {"tool_call_count": len(trace.tool_calls)},
        {"tool_calls": [model_to_dict(call) for call in trace.tool_calls]},
    )
    _span(
        trace_obj,
        "evidence_scoring",
        {"citations": [model_to_dict(source) for source in trace.citations]},
        {"evidence_scores": [model_to_dict(item) for item in trace.evidence_scores]},
    )
    _span(
        trace_obj,
        "gap_handling",
        {"cross_source_consistency": trace.cross_source_consistency},
        {"unverified_gaps": trace.unverified_gaps, "notes": trace.notes},
    )
    _span(trace_obj, "final_answer", {"query": trace.query}, {"final_answer": trace.final_answer})

    if hasattr(langfuse, "flush"):
        langfuse.flush()

    return getattr(trace_obj, "id", None)


def _upload_modern_trace(langfuse: Any, trace: ResearchTrace) -> str | None:
    trace_id = None
    root_name = f"deep_research_comparison::{trace.engine_name}"
    root_metadata = {
        "engine_name": trace.engine_name,
        "query": trace.query,
        "detected_jurisdictions": trace.detected_jurisdictions,
    }
    with langfuse.start_as_current_observation(
        name=root_name,
        as_type="span",
        input={"query": trace.query},
        output={"final_answer": trace.final_answer},
        metadata=root_metadata,
    ) as root_span:
        trace_id = _get_trace_id(langfuse, root_span)
        _modern_span(root_span, "research_plan", {"query": trace.query}, {"plan": trace.research_plan})
        _modern_span(root_span, "query_generation", {"query": trace.query}, {"generated_queries": trace.generated_queries})
        _modern_span(
            root_span,
            "official_source_search",
            {"official_source_queries": trace.official_source_queries},
            {"searched_sources": trace.searched_sources, "sources_found": [model_to_dict(source) for source in trace.sources_found]},
        )
        _modern_span(
            root_span,
            "tool_calls",
            {"tool_call_count": len(trace.tool_calls)},
            {"tool_calls": [model_to_dict(call) for call in trace.tool_calls]},
        )
        _modern_span(
            root_span,
            "evidence_scoring",
            {"citations": [model_to_dict(source) for source in trace.citations]},
            {"evidence_scores": [model_to_dict(item) for item in trace.evidence_scores]},
        )
        _modern_span(
            root_span,
            "gap_handling",
            {"cross_source_consistency": trace.cross_source_consistency},
            {"unverified_gaps": trace.unverified_gaps, "notes": trace.notes},
        )
        _modern_span(root_span, "final_answer", {"query": trace.query}, {"final_answer": trace.final_answer})

    if hasattr(langfuse, "flush"):
        langfuse.flush()
    return trace_id


def _span(trace_obj: Any, name: str, span_input: dict[str, Any], span_output: dict[str, Any]) -> None:
    span = trace_obj.span(name=name, input=span_input, output=span_output)
    if hasattr(span, "end"):
        span.end()


def _modern_span(root_span: Any, name: str, span_input: dict[str, Any], span_output: dict[str, Any]) -> None:
    try:
        context = root_span.start_as_current_observation(
            name=name,
            as_type="span",
            input=span_input,
            output=span_output,
        )
    except TypeError:
        context = root_span.start_as_current_observation(
            name=name,
            input=span_input,
            output=span_output,
        )
    with context:
        pass


def _get_trace_id(langfuse: Any, root_span: Any) -> str | None:
    if hasattr(langfuse, "get_current_trace_id"):
        return langfuse.get_current_trace_id()
    return getattr(root_span, "trace_id", None) or getattr(root_span, "id", None)
