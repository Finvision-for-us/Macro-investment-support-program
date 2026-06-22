from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from gemini_client import extract_citations, make_json_safe, read_text_file
from gemini_deep_research_client import _poll_interaction
from html_log_viewer import write_html_log_viewer
from live_log import LiveLogWriter
from schema import GeminiRunRecord, TraceEventItem, model_to_dict


BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    prompt_text = read_text_file(BASE_DIR / "prompts" / "user_questions" / "indi_wuxi.txt")
    assert "매각" in prompt_text

    raw = {
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "nested": {"date": date(2026, 6, 1)},
        "url": "https://www.sec.gov/example",
        "text": prompt_text,
    }
    safe_raw = make_json_safe(raw)
    json.dumps(safe_raw, ensure_ascii=False)
    citations = extract_citations(raw)
    poll_result = _poll_interaction(
        get_response=lambda: raw,
        initial_response={
            "id": "interaction-smoke",
            "status": "completed",
            "created_at": datetime.now(timezone.utc),
            "output_text": "한글 deep research 완료",
            "url": "https://www.sec.gov/example",
        },
        interaction_id="interaction-smoke",
        request_metadata={"mode": "deep-research", "created_at": datetime.now(timezone.utc)},
        events=[],
        poll_interval=0,
        timeout_seconds=1,
    )
    json.dumps(make_json_safe(poll_result), ensure_ascii=False)
    assert poll_result["status"] == "success"
    assert "한글" in poll_result["final_answer"]

    record = GeminiRunRecord(
        run_id="smoke",
        query=prompt_text,
        mode="deep-research",
        model="",
        agent="deep-research-preview-04-2026",
        interaction_id="interaction-smoke",
        poll_count=1,
        polling_interval_seconds=10.0,
        timeout_seconds=600,
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        status="success",
        request_metadata={"created_at": datetime.now(timezone.utc)},
        events=[
            TraceEventItem(
                name="interaction_poll",
                input={"when": datetime.now(timezone.utc)},
                output_summary="status=completed",
                metadata={"date": date.today()},
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        ],
        citations=citations,
        final_answer="한글 출력 정상",
        notes=["smoke check"],
    )
    json.dumps(make_json_safe(model_to_dict(record)), ensure_ascii=False)

    with tempfile.TemporaryDirectory() as temp_dir:
        live = LiveLogWriter(output_dir=temp_dir, enabled=True, open_viewer=False, refresh_seconds=1)
        live.start({"run_id": "smoke", "mode": "deep-research"})
        live.emit("instruction_loaded", path="instruction.txt", chars=10)
        live.emit("interaction_poll_result", poll_count=1, interaction_id="interaction-smoke", status="completed", summary="한글 상태")
        live.emit("run_finished", status="success")
        assert (Path(temp_dir) / "live_log.jsonl").exists()
        live_html = (Path(temp_dir) / "live_log_viewer.html").read_text(encoding="utf-8")
        assert "한글 상태" in live_html

        html_path = Path(temp_dir) / "viewer.html"
        write_html_log_viewer(
            output_path=html_path,
            record=record,
            instruction_text="금융 리서치 instruction",
            prompt_text=prompt_text,
            raw_response=raw,
        )
        html = html_path.read_text(encoding="utf-8")
        assert "한글 출력 정상" in html
        assert "매각" in html

    print("smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
