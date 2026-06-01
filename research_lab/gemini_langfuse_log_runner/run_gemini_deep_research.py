from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gemini_client import GeminiExecutionError, read_text_file, resolve_model, run_gemini_deep_research
from html_log_viewer import write_html_log_viewer
from langfuse_client import get_langfuse_state
from schema import GeminiRunRecord, TraceEventItem, model_to_dict
from trace_builder import upload_record_to_langfuse


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gemini Deep Research with a financial instruction prompt.")
    parser.add_argument("--instruction", required=True, help="Path to financial research instruction text file.")
    parser.add_argument("--prompt", required=True, help="Path to user question prompt text file.")
    parser.add_argument("--model", help="Gemini model name. Overrides GEMINI_DEEP_RESEARCH_MODEL.")
    parser.add_argument("--no-langfuse", action="store_true", help="Skip optional Langfuse upload.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for output files.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_iso()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    model = resolve_model(args.model)
    instruction_path = str(Path(args.instruction))
    prompt_path = str(Path(args.prompt))
    instruction_text = read_text_file(args.instruction)
    prompt_text = read_text_file(args.prompt)

    events = [
        event("financial_instruction", None, f"{len(instruction_text)} chars loaded.", {"path": instruction_path}),
        event("user_prompt", None, prompt_text, {"path": prompt_path}),
        event("gemini_request", {"model": model, "prompt": prompt_text}, "Gemini request prepared.", {"model": model}),
    ]

    raw_response: dict = {}
    final_answer = ""
    citations = []
    notes: list[str] = []
    request_metadata = {"model": model}
    status = "success"

    try:
        result = run_gemini_deep_research(instruction=instruction_text, user_prompt=prompt_text, model=model)
        raw_response = result["raw_response"]
        final_answer = result["final_answer"]
        citations = result["citations"]
        request_metadata = result["request_metadata"]
        notes.extend(result.get("notes", []))
        events.append(event("gemini_response_raw", None, "Gemini response captured.", {"raw_keys": list(raw_response.keys())}))
        events.append(event("citations", None, f"{len(citations)} citations extracted.", {}))
        events.append(event("final_answer", None, final_answer[:1000], {"chars": len(final_answer)}))
    except Exception as exc:
        status = "error"
        message = str(exc)
        if not isinstance(exc, GeminiExecutionError):
            message = f"Unexpected runner error: {message}"
        notes.append(message)
        raw_response = {"error": message, "status": "error", "request_metadata": request_metadata}
        events.append(event("error_if_any", None, message, {"error_type": exc.__class__.__name__}))

    raw_path = output_dir / "gemini_run_raw.json"
    record_path = output_dir / "gemini_run_record.json"
    summary_path = output_dir / "gemini_run_summary.md"
    html_path = output_dir / "gemini_run_log_viewer.html"

    record = GeminiRunRecord(
        run_id=run_id,
        query=prompt_text.strip(),
        instruction_path=instruction_path,
        prompt_path=prompt_path,
        model=model,
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

    raw_path.write_text(json.dumps(raw_response, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary_path.write_text(render_summary(record), encoding="utf-8")
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
        summary_path.write_text(render_summary(record, langfuse_result), encoding="utf-8")
        write_html_log_viewer(
            output_path=html_path,
            record=record,
            instruction_text=instruction_text,
            prompt_text=prompt_text,
            raw_response=raw_response,
        )

    record_path.write_text(json.dumps(model_to_dict(record), ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"raw JSON: {raw_path}")
    print(f"run record: {record_path}")
    print(f"summary markdown: {summary_path}")
    print(f"HTML log viewer: {html_path}")
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
        f"- Status: {record.status}",
        f"- Model: {record.model}",
        f"- Started: {record.started_at}",
        f"- Ended: {record.ended_at}",
        f"- Instruction: {record.instruction_path}",
        f"- Prompt: {record.prompt_path}",
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
        lines.extend(["", "## Langfuse", "", "```json", json.dumps(langfuse_result, ensure_ascii=False, indent=2), "```"])

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
