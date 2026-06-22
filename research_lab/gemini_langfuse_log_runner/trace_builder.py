from __future__ import annotations

import json
from typing import Any

from gemini_client import make_json_safe
from langfuse_client import LangfuseState, warn
from schema import GeminiRunRecord, model_to_dict


def upload_record_to_langfuse(
    *,
    state: LangfuseState,
    record: GeminiRunRecord,
    instruction_text: str,
    prompt_text: str,
    raw_response: dict[str, Any],
) -> dict[str, Any]:
    if not state.enabled or state.client is None:
        return {"uploaded": False, "message": state.message or "Langfuse disabled."}

    try:
        client = state.client
        if hasattr(client, "start_as_current_observation"):
            trace_id = _upload_modern(client, record, instruction_text, prompt_text, raw_response)
        else:
            trace_id = _upload_legacy(client, record, instruction_text, prompt_text, raw_response)
        trace_url = _build_trace_url(state.host, trace_id)
        return {"uploaded": True, "trace_id": trace_id, "trace_url": trace_url}
    except Exception as exc:
        warn(f"Langfuse upload failed but local files were saved: {exc}")
        return {"uploaded": False, "message": f"Langfuse upload failed: {exc}"}


def _metadata(record: GeminiRunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "mode": record.mode,
        "model": record.model,
        "agent": record.agent,
        "interaction_id": record.interaction_id,
        "query": record.query,
        "status": record.status,
        "instruction_path": record.instruction_path,
        "prompt_path": record.prompt_path,
    }


def _upload_legacy(
    client: Any,
    record: GeminiRunRecord,
    instruction_text: str,
    prompt_text: str,
    raw_response: dict[str, Any],
) -> str | None:
    trace = client.trace(
        name=_trace_name(record),
        input={"query": record.query},
        output={"final_answer": record.final_answer},
        metadata=_metadata(record),
    )
    _legacy_span(trace, "mode_explanation", None, _mode_explanation(record.mode))
    _legacy_span(trace, "financial_instruction", None, instruction_text)
    _legacy_span(trace, "user_prompt", None, prompt_text)
    _legacy_span(trace, "request_metadata", record.request_metadata, pretty(record.request_metadata))
    _legacy_span(trace, "polling_events", None, _polling_events(record))
    _legacy_span(trace, "citations", None, pretty([model_to_dict(item) for item in record.citations]))
    _legacy_span(trace, "final_answer", None, record.final_answer)
    _legacy_span(trace, "raw_response_preview", None, pretty(raw_response)[:20000])
    if record.status != "success":
        _legacy_span(trace, "error_if_any", None, pretty(record.notes))
    if hasattr(client, "flush"):
        client.flush()
    return getattr(trace, "id", None)


def _upload_modern(
    client: Any,
    record: GeminiRunRecord,
    instruction_text: str,
    prompt_text: str,
    raw_response: dict[str, Any],
) -> str | None:
    with client.start_as_current_observation(
        name=_trace_name(record),
        as_type="span",
        input={"query": record.query},
        output={"final_answer": record.final_answer},
        metadata=_metadata(record),
    ) as root:
        trace_id = _current_trace_id(client, root)
        _modern_span(root, "mode_explanation", None, _mode_explanation(record.mode))
        _modern_span(root, "financial_instruction", None, instruction_text)
        _modern_span(root, "user_prompt", None, prompt_text)
        _modern_span(root, "request_metadata", record.request_metadata, pretty(record.request_metadata))
        _modern_span(root, "polling_events", None, _polling_events(record))
        _modern_span(root, "citations", None, pretty([model_to_dict(item) for item in record.citations]))
        _modern_span(root, "final_answer", None, record.final_answer)
        _modern_span(root, "raw_response_preview", None, pretty(raw_response)[:20000])
        if record.status != "success":
            _modern_span(root, "error_if_any", None, pretty(record.notes))
    if hasattr(client, "flush"):
        client.flush()
    return trace_id


def _legacy_span(trace: Any, name: str, span_input: Any, span_output: str) -> None:
    span = trace.span(name=name, input=span_input, output=span_output)
    if hasattr(span, "end"):
        span.end()


def _modern_span(root: Any, name: str, span_input: Any, span_output: str) -> None:
    try:
        context = root.start_as_current_observation(name=name, as_type="span", input=span_input, output=span_output)
    except TypeError:
        context = root.start_as_current_observation(name=name, input=span_input, output=span_output)
    with context:
        pass


def _current_trace_id(client: Any, root: Any) -> str | None:
    if hasattr(client, "get_current_trace_id"):
        return client.get_current_trace_id()
    return getattr(root, "trace_id", None) or getattr(root, "id", None)


def _build_trace_url(host: str | None, trace_id: str | None) -> str | None:
    if not host or not trace_id:
        return None
    return host.rstrip("/") + f"/trace/{trace_id}"


def pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(make_json_safe(value), ensure_ascii=False, indent=2, default=str)


def _trace_name(record: GeminiRunRecord) -> str:
    if record.mode == "deep-research":
        return "gemini_deep_research_financial_run"
    return "gemini_grounded_financial_research_run"


def _mode_explanation(mode: str) -> str:
    if mode == "deep-research":
        return "This run used Gemini Deep Research Agent via the Interactions API."
    return (
        "This run used Gemini generate_content with Google Search grounding. "
        "This is not the Gemini Deep Research Agent."
    )


def _polling_events(record: GeminiRunRecord) -> str:
    events = [
        model_to_dict(event)
        for event in record.events
        if event.name.startswith("interaction_") or event.name == "sdk_interactions_missing"
    ]
    return pretty(events)
