from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from gemini_client import GeminiExecutionError, extract_citations, extract_final_answer, format_gemini_error, require_api_key, response_to_jsonable
from schema import TraceEventItem


MODE_EXPLANATION = "This run used Gemini Deep Research Agent via the Interactions API."
INTERACTIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
API_REVISION = "2026-05-20"
TERMINAL_SUCCESS = {"completed", "succeeded", "complete", "done"}
TERMINAL_FAILURE = {"failed", "cancelled", "canceled", "expired"}


def default_agent_config() -> dict[str, Any]:
    return {
        "type": "deep-research",
        "thinking_summaries": "auto",
        "visualization": "auto",
        "collaborative_planning": False,
    }


def run_deep_research(
    *,
    instruction: str,
    user_prompt: str,
    agent: str,
    poll_interval: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    api_key = require_api_key()
    request_input = _combined_prompt(instruction, user_prompt)
    agent_config = default_agent_config()
    request_metadata = {
        "mode": "deep-research",
        "api": "interactions",
        "agent": agent,
        "background": True,
        "store": True,
        "agent_config": agent_config,
        "instruction_chars": len(instruction),
        "prompt_chars": len(user_prompt),
        "mode_explanation": MODE_EXPLANATION,
    }
    events = [_event("mode_explanation", None, MODE_EXPLANATION, {"mode": "deep-research"})]

    try:
        from google import genai
    except ImportError as exc:
        raise GeminiExecutionError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client(api_key=api_key)
    if hasattr(client, "interactions"):
        return _run_with_sdk(
            client=client,
            request_input=request_input,
            agent=agent,
            agent_config=agent_config,
            request_metadata=request_metadata,
            events=events,
            poll_interval=poll_interval,
            timeout_seconds=timeout_seconds,
        )

    events.append(_event(
        "sdk_interactions_missing",
        None,
        "Current google-genai SDK does not expose client.interactions; trying REST fallback.",
        {"fallback": "rest", "api_revision": API_REVISION},
    ))
    return _run_with_rest(
        api_key=api_key,
        request_input=request_input,
        agent=agent,
        agent_config=agent_config,
        request_metadata=request_metadata,
        events=events,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )


def _run_with_sdk(
    *,
    client: Any,
    request_input: str,
    agent: str,
    agent_config: dict[str, Any],
    request_metadata: dict[str, Any],
    events: list[TraceEventItem],
    poll_interval: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        create_kwargs = {
            "input": request_input,
            "agent": agent,
            "background": True,
            "store": True,
            "agent_config": agent_config,
        }
        interaction = client.interactions.create(**create_kwargs)
    except TypeError:
        interaction = client.interactions.create(
            input=request_input,
            agent=agent,
            background=True,
            agent_config=agent_config,
        )
    except Exception as exc:
        raise GeminiExecutionError(format_gemini_error(exc)) from exc

    interaction_id = _extract_interaction_id(interaction)
    if not interaction_id:
        raise GeminiExecutionError("Interactions API did not return an interaction id.")

    events.append(_event("interaction_create", {"agent": agent, "background": True}, f"interaction_id={interaction_id}", {}))
    return _poll_interaction(
        get_response=lambda: client.interactions.get(interaction_id),
        initial_response=interaction,
        interaction_id=interaction_id,
        request_metadata=request_metadata,
        events=events,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )


def _run_with_rest(
    *,
    api_key: str,
    request_input: str,
    agent: str,
    agent_config: dict[str, Any],
    request_metadata: dict[str, Any],
    events: list[TraceEventItem],
    poll_interval: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    body = {
        "input": request_input,
        "agent": agent,
        "background": True,
        "store": True,
        "agent_config": agent_config,
    }
    create_raw = _request_json("POST", INTERACTIONS_ENDPOINT, api_key, body)
    interaction_id = _extract_interaction_id(create_raw)
    if not interaction_id:
        raise GeminiExecutionError(
            "Interactions REST fallback did not return an interaction id. "
            "Update google-genai or check Gemini Interactions API availability."
        )

    events.append(_event("interaction_create_rest", {"agent": agent, "background": True}, f"interaction_id={interaction_id}", {}))
    return _poll_interaction(
        get_response=lambda: _request_json("GET", f"{INTERACTIONS_ENDPOINT}/{interaction_id}", api_key, None),
        initial_response=create_raw,
        interaction_id=interaction_id,
        request_metadata=request_metadata,
        events=events,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )


def _poll_interaction(
    *,
    get_response,
    initial_response: Any,
    interaction_id: str,
    request_metadata: dict[str, Any],
    events: list[TraceEventItem],
    poll_interval: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    poll_count = 0
    current = initial_response
    raw_history = [response_to_jsonable(current) if not isinstance(current, dict) else current]

    while True:
        current_raw = response_to_jsonable(current) if not isinstance(current, dict) else current
        status = _extract_status(current_raw)
        events.append(_event(
            "interaction_poll",
            {"interaction_id": interaction_id, "poll_count": poll_count},
            f"status={status or 'unknown'}",
            {"status": status, "poll_count": poll_count},
        ))

        if (status or "").lower() in TERMINAL_SUCCESS:
            final_answer = _extract_output_text(current, current_raw)
            return {
                "raw_response": {"interaction_id": interaction_id, "poll_history": raw_history, "final": current_raw},
                "final_answer": final_answer,
                "citations": extract_citations(current_raw),
                "request_metadata": request_metadata,
                "events": events,
                "notes": [MODE_EXPLANATION],
                "interaction_id": interaction_id,
                "poll_count": poll_count,
                "status": "success",
            }

        if (status or "").lower() in TERMINAL_FAILURE:
            error_text = _extract_error(current_raw) or f"Interaction ended with status={status}"
            return {
                "raw_response": {"interaction_id": interaction_id, "poll_history": raw_history, "final": current_raw},
                "final_answer": "",
                "citations": extract_citations(current_raw),
                "request_metadata": request_metadata,
                "events": events,
                "notes": [MODE_EXPLANATION, error_text],
                "interaction_id": interaction_id,
                "poll_count": poll_count,
                "status": "error",
            }

        if time.monotonic() - started > timeout_seconds:
            timeout_text = f"Timed out waiting for interaction {interaction_id} after {timeout_seconds} seconds."
            return {
                "raw_response": {"interaction_id": interaction_id, "poll_history": raw_history, "final": current_raw},
                "final_answer": "",
                "citations": extract_citations(current_raw),
                "request_metadata": request_metadata,
                "events": events,
                "notes": [MODE_EXPLANATION, timeout_text],
                "interaction_id": interaction_id,
                "poll_count": poll_count,
                "status": "error",
            }

        time.sleep(poll_interval)
        poll_count += 1
        current = get_response()
        raw_history.append(response_to_jsonable(current) if not isinstance(current, dict) else current)


def _request_json(method: str, url: str, api_key: str, body: dict[str, Any] | None) -> dict[str, Any]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url=url,
        data=payload,
        method=method,
        headers={
            "x-goog-api-key": api_key,
            "Api-Revision": API_REVISION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GeminiExecutionError(f"Interactions REST request failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise GeminiExecutionError(f"Interactions REST request failed: {exc}") from exc


def _combined_prompt(instruction: str, user_prompt: str) -> str:
    return (
        "[Financial Research Instruction]\n"
        f"{instruction.strip()}\n\n"
        "[User Question]\n"
        f"{user_prompt.strip()}"
    )


def _extract_interaction_id(value: Any) -> str | None:
    raw = response_to_jsonable(value) if not isinstance(value, dict) else value
    for key in ("id", "name", "interaction_id", "interactionId"):
        found = raw.get(key)
        if isinstance(found, str) and found:
            return found.rsplit("/", 1)[-1]
    return None


def _extract_status(raw: dict[str, Any]) -> str | None:
    for key in ("status", "state"):
        value = raw.get(key)
        if isinstance(value, str):
            return value.lower()
        if isinstance(value, dict):
            for nested in ("status", "state", "name"):
                nested_value = value.get(nested)
                if isinstance(nested_value, str):
                    return nested_value.lower()
    return None


def _extract_output_text(response: Any, raw: dict[str, Any]) -> str:
    for attr in ("output_text", "text"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("output_text", "text", "final_answer", "answer"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return extract_final_answer(response, raw)


def _extract_error(raw: dict[str, Any]) -> str | None:
    error = raw.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return json.dumps(error, ensure_ascii=False)
    return None


def _event(name: str, input_value: Any, output_summary: str | None, metadata: dict[str, Any]) -> TraceEventItem:
    return TraceEventItem(
        name=name,
        input=input_value,
        output_summary=output_summary,
        metadata=metadata,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
