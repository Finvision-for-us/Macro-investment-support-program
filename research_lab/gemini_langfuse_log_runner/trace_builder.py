from __future__ import annotations

import json
from typing import Any

from langfuse_client import LangfuseState, warn
from schema import GeminiRunRecord, model_to_dict


TRACE_NAME = "gemini_deep_research_financial_prompt_run"


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
        "model": record.model,
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
        name=TRACE_NAME,
        input={"query": record.query},
        output={"final_answer": record.final_answer},
        metadata=_metadata(record),
    )
    _legacy_span(trace, "financial_instruction", None, instruction_text)
    _legacy_span(trace, "user_prompt", None, prompt_text)
    _legacy_span(trace, "gemini_request", record.request_metadata, pretty(record.request_metadata))
    _legacy_span(trace, "gemini_response_raw", None, pretty(raw_response))
    _legacy_span(trace, "citations", None, pretty([model_to_dict(item) for item in record.citations]))
    _legacy_span(trace, "final_answer", None, record.final_answer)
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
        name=TRACE_NAME,
        as_type="span",
        input={"query": record.query},
        output={"final_answer": record.final_answer},
        metadata=_metadata(record),
    ) as root:
        trace_id = _current_trace_id(client, root)
        _modern_span(root, "financial_instruction", None, instruction_text)
        _modern_span(root, "user_prompt", None, prompt_text)
        _modern_span(root, "gemini_request", record.request_metadata, pretty(record.request_metadata))
        _modern_span(root, "gemini_response_raw", None, pretty(raw_response))
        _modern_span(root, "citations", None, pretty([model_to_dict(item) for item in record.citations]))
        _modern_span(root, "final_answer", None, record.final_answer)
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
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
