from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gemini_client import GeminiExecutionError, make_json_safe, read_text_file, resolve_deep_research_agent, resolve_grounded_model
from gemini_deep_research_client import run_deep_research
from gemini_grounded_client import run_grounded_research
from html_log_viewer import write_html_log_viewer
from langfuse_client import get_langfuse_state
from live_log import LiveLogWriter
from schema import GeminiRunRecord, TraceEventItem, model_to_dict
from trace_builder import upload_record_to_langfuse


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_ENCODING = "utf-8-sig"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gemini financial research in grounded or deep-research mode.")
    parser.add_argument("--mode", choices=["grounded", "deep-research"], default="grounded", help="Execution mode.")
    parser.add_argument("--instruction", required=True, help="Path to financial research instruction text file.")
    parser.add_argument("--prompt", required=True, help="Path to user question prompt text file.")
    parser.add_argument("--model", help="Grounded mode model name. Overrides GEMINI_GROUNDED_MODEL.")
    parser.add_argument("--agent", help="Deep Research mode agent name. Overrides GEMINI_DEEP_RESEARCH_AGENT.")
    parser.add_argument("--no-langfuse", action="store_true", help="Skip optional Langfuse upload.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for output files.")
    parser.add_argument("--poll-interval", type=float, default=10.0, help="Deep Research polling interval in seconds.")
    parser.add_argument("--timeout", type=int, default=3600, help="Deep Research timeout in seconds.")
    parser.add_argument("--live-log", dest="live_log", action="store_true", default=None, help="Enable local live log files.")
    parser.add_argument("--no-live-log", dest="live_log", action="store_false", help="Disable local live log files.")
    parser.add_argument("--open-live-log", dest="open_live_log", action="store_true", default=None, help="Open live log viewer automatically.")
    parser.add_argument("--no-open-live-log", dest="open_live_log", action="store_false", help="Do not open live log viewer automatically.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_iso()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    mode = args.mode
    live_enabled = args.live_log if args.live_log is not None else mode == "deep-research"
    open_live_log = args.open_live_log if args.open_live_log is not None else True
    live_logger = LiveLogWriter(output_dir=output_dir, enabled=live_enabled, open_viewer=open_live_log)
    model = resolve_grounded_model(args.model) if mode == "grounded" else ""
    agent = resolve_deep_research_agent(args.agent) if mode == "deep-research" else None
    instruction_path = str(Path(args.instruction))
    prompt_path = str(Path(args.prompt))
    live_logger.start({
        "run_id": run_id,
        "mode": mode,
        "model": model,
        "agent": agent,
        "output_dir": str(output_dir),
        "live_log_jsonl": str(live_logger.jsonl_path),
        "live_log_viewer": str(live_logger.viewer_path),
    })
    instruction_text = ""
    prompt_text = ""
    initialization_error: Exception | None = None
    try:
        instruction_text = read_text_file(args.instruction)
        live_logger.emit("instruction_loaded", path=instruction_path, chars=len(instruction_text))
        prompt_text = read_text_file(args.prompt)
        live_logger.emit("prompt_loaded", path=prompt_path, chars=len(prompt_text), preview=prompt_text[:500])
    except Exception as exc:
        initialization_error = exc

    mode_explanation = mode_explanation_for(mode)
    events = [
        event("financial_instruction", None, f"{len(instruction_text)} chars loaded.", {"path": instruction_path}),
        event("user_prompt", None, prompt_text, {"path": prompt_path}),
        event("mode_explanation", None, mode_explanation, {"mode": mode}),
        event(
            "gemini_request",
            {"mode": mode, "model": model, "agent": agent, "prompt": prompt_text},
            "Gemini request prepared.",
            {"mode": mode, "model": model, "agent": agent},
        ),
    ]
    live_logger.emit(
        "gemini_request_prepared",
        mode=mode,
        model=model,
        agent=agent,
        poll_interval=args.poll_interval if mode == "deep-research" else None,
        timeout=args.timeout if mode == "deep-research" else None,
    )

    raw_response: dict = {}
    final_answer = ""
    citations = []
    notes: list[str] = []
    request_metadata = initial_request_metadata(mode=mode, model=model, agent=agent)
    interaction_id = None
    poll_count = None
    status = "success"

    try:
        if initialization_error is not None:
            raise initialization_error
        if mode == "grounded":
            result = run_grounded_research(instruction=instruction_text, user_prompt=prompt_text, model=model)
        else:
            result = run_deep_research(
                instruction=instruction_text,
                user_prompt=prompt_text,
                agent=agent or "",
                poll_interval=args.poll_interval,
                timeout_seconds=args.timeout,
                live_logger=live_logger if live_enabled else None,
            )
        raw_response = make_json_safe(result["raw_response"])
        final_answer = result["final_answer"]
        citations = result["citations"]
        request_metadata = make_json_safe(result["request_metadata"])
        events.extend(result.get("events", []))
        notes.extend(result.get("notes", []))
        interaction_id = result.get("interaction_id")
        poll_count = result.get("poll_count")
        status = result.get("status", status)
        if status == "success":
            live_logger.emit("final_success", mode=mode, interaction_id=interaction_id, poll_count=poll_count, summary=final_answer[:500])
        else:
            live_logger.emit("final_error", mode=mode, interaction_id=interaction_id, poll_count=poll_count, summary="; ".join(notes[-3:]))
        events.append(event("gemini_response_raw", None, "Gemini response captured.", {"raw_keys": list(raw_response.keys())}))
        events.append(event("citations", None, f"{len(citations)} citations extracted.", {}))
        events.append(event("final_answer", None, final_answer[:1000], {"chars": len(final_answer)}))
    except Exception as exc:
        status = "error"
        message = str(exc)
        if not isinstance(exc, GeminiExecutionError):
            message = f"Unexpected runner error: {message}"
        notes.append(message)
        raw_response = make_json_safe({"error": message, "status": "error", "request_metadata": request_metadata})
        events.append(event("error_if_any", None, message, {"error_type": exc.__class__.__name__}))
        live_logger.emit("final_error", mode=mode, summary=message, error_type=exc.__class__.__name__)

    raw_path = output_dir / "gemini_run_raw.json"
    record_path = output_dir / "gemini_run_record.json"
    summary_path = output_dir / "gemini_run_summary.md"
    html_path = output_dir / "gemini_run_log_viewer.html"

    record = GeminiRunRecord(
        run_id=run_id,
        query=prompt_text.strip(),
        instruction_path=instruction_path,
        prompt_path=prompt_path,
        mode=mode,
        model=model,
        agent=agent,
        interaction_id=interaction_id,
        poll_count=poll_count,
        polling_interval_seconds=args.poll_interval if mode == "deep-research" else None,
        timeout_seconds=args.timeout if mode == "deep-research" else None,
        started_at=started_at,
        ended_at=now_iso(),
        status=status,
        request_metadata=request_metadata,
        events=events,
        citations=citations,
        final_answer=final_answer,
        raw_response_path=str(raw_path),
        summary_path=str(summary_path),
        html_log_viewer_path=str(html_path),
        notes=notes,
    )

    raw_response = make_json_safe(raw_response)
    raw_path.write_text(json.dumps(raw_response, ensure_ascii=False, indent=2, default=str), encoding=OUTPUT_ENCODING)
    summary_path.write_text(render_summary(record), encoding=OUTPUT_ENCODING)
    write_html_log_viewer(
        output_path=html_path,
        record=record,
        instruction_text=instruction_text,
        prompt_text=prompt_text,
        raw_response=raw_response,
    )

    langfuse_result = {"uploaded": False, "message": "Langfuse skipped by --no-langfuse."}
    if not args.no_langfuse:
        state = get_langfuse_state()
        langfuse_result = upload_record_to_langfuse(
            state=state,
            record=record,
            instruction_text=instruction_text,
            prompt_text=prompt_text,
            raw_response=raw_response,
        )
        record.notes.append(f"Langfuse: {langfuse_result}")
        summary_path.write_text(render_summary(record, langfuse_result), encoding=OUTPUT_ENCODING)
        write_html_log_viewer(
            output_path=html_path,
            record=record,
            instruction_text=instruction_text,
            prompt_text=prompt_text,
            raw_response=raw_response,
        )

    record_path.write_text(json.dumps(make_json_safe(model_to_dict(record)), ensure_ascii=False, indent=2, default=str), encoding=OUTPUT_ENCODING)
    live_logger.emit("run_finished", status=status, raw_path=str(raw_path), record_path=str(record_path), summary_path=str(summary_path), html_path=str(html_path))

    print(f"raw JSON: {raw_path}")
    print(f"run record: {record_path}")
    print(f"summary markdown: {summary_path}")
    print(f"HTML log viewer: {html_path}")
    print(f"mode: {mode}")
    if mode == "grounded":
        print(f"model: {model}")
    else:
        print(f"agent: {agent}")
        print(f"interaction id: {interaction_id or '(none)'}")
    print(f"Langfuse: {langfuse_result}")
    print("Open the HTML file in a browser and drag-copy the log sections you need.")

    return 0 if status == "success" else 1


def event(name: str, input_value, output_summary: str | None, metadata: dict) -> TraceEventItem:
    return TraceEventItem(
        name=name,
        input=input_value,
        output_summary=output_summary,
        metadata=metadata,
        timestamp=now_iso(),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def render_summary(record: GeminiRunRecord, langfuse_result: dict | None = None) -> str:
    lines = [
        "# Gemini Deep Research Financial Run",
        "",
        f"- Run ID: {record.run_id}",
        f"- Mode: {record.mode}",
        f"- Status: {record.status}",
        f"- Model: {record.model or '(none)'}",
        f"- Agent: {record.agent or '(none)'}",
        f"- Interaction ID: {record.interaction_id or '(none)'}",
        f"- Started: {record.started_at}",
        f"- Ended: {record.ended_at}",
        f"- Instruction: {record.instruction_path}",
        f"- Prompt: {record.prompt_path}",
        "",
        "## Mode Explanation",
        "",
        mode_explanation_for(record.mode),
        "",
        "## User Prompt",
        "",
        record.query,
        "",
        "## Final Answer",
        "",
        record.final_answer or "(empty)",
        "",
        "## Citations",
        "",
    ]
    if record.citations:
        for item in record.citations:
            lines.append(f"- {item.title or '(untitled)'}: {item.url or '(no url)'}")
    else:
        lines.append("- (none extracted)")

    lines.extend(["", "## Notes", ""])
    if record.notes:
        lines.extend([f"- {note}" for note in record.notes])
    else:
        lines.append("- (none)")

    if langfuse_result is not None:
        lines.extend(["", "## Langfuse", "", "```json", json.dumps(make_json_safe(langfuse_result), ensure_ascii=False, indent=2, default=str), "```"])

    return "\n".join(lines) + "\n"


def mode_explanation_for(mode: str) -> str:
    if mode == "deep-research":
        return "This run used Gemini Deep Research Agent via the Interactions API."
    return (
        "This run used Gemini generate_content with Google Search grounding. "
        "This is not the Gemini Deep Research Agent."
    )


def initial_request_metadata(mode: str, model: str, agent: str | None) -> dict:
    if mode == "deep-research":
        return {
            "mode": "deep-research",
            "api": "interactions",
            "agent": agent,
            "background": True,
            "store": True,
            "agent_config": {
                "type": "deep-research",
                "thinking_summaries": "auto",
                "visualization": "auto",
                "collaborative_planning": False,
            },
        }
    return {
        "mode": "grounded",
        "api": "generate_content",
        "model": model,
        "uses_google_search_grounding": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
