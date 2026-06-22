from __future__ import annotations

import html
import json
import os
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gemini_client import make_json_safe


VIEWER_ENCODING = "utf-8-sig"


class LiveLogWriter:
    def __init__(
        self,
        *,
        output_dir: str | Path,
        enabled: bool,
        open_viewer: bool,
        refresh_seconds: int = 2,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.enabled = enabled
        self.open_viewer = open_viewer
        self.refresh_seconds = refresh_seconds
        self.jsonl_path = self.output_dir / "live_log.jsonl"
        self.viewer_path = self.output_dir / "live_log_viewer.html"
        self.events: list[dict[str, Any]] = []
        self.current_status = "starting"
        self.opened = False

    def start(self, metadata: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.write_text("", encoding="utf-8")
        self._render()
        self.emit("run_started", metadata=metadata)
        if self.open_viewer:
            self.open()

    def emit(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        entry = make_json_safe({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        })
        self.current_status = _status_from_event(event, self.current_status)
        self.events.append(entry)
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self._render()

    def open(self) -> None:
        if self.opened:
            return
        try:
            os.startfile(str(self.viewer_path))  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(self.viewer_path.resolve().as_uri())
        self.opened = True

    def _render(self) -> None:
        rows = "\n".join(_event_block(event) for event in reversed(self.events))
        if not rows:
            rows = "<section><h2>No events yet</h2><pre>Waiting for run events...</pre></section>"
        last_updated = datetime.now(timezone.utc).isoformat()
        document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{self.refresh_seconds}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gemini Live Log Viewer</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #627084;
      --border: #d8dee8;
      --accent: #1456d9;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Arial, "Malgun Gothic", sans-serif;
      line-height: 1.5;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #fff;
      border-bottom: 1px solid var(--border);
      padding: 14px 18px;
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 18px;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 16px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 12px;
      overflow: hidden;
    }}
    h2 {{
      margin: 0;
      padding: 10px 12px;
      font-size: 14px;
      background: #fbfcfe;
      border-bottom: 1px solid var(--border);
    }}
    pre {{
      margin: 0;
      padding: 12px;
      white-space: pre-wrap;
      word-break: break-word;
      overflow: auto;
      font: 13px/1.55 Consolas, "Courier New", "Malgun Gothic", monospace;
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .status {{
      display: inline-block;
      margin-right: 8px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Gemini Deep Research Live Log</h1>
    <div class="meta"><span class="status">{html.escape(self.current_status)}</span>Auto-refresh every {self.refresh_seconds}s. Last updated: {html.escape(last_updated)}</div>
    <div class="meta">JSONL: {html.escape(str(self.jsonl_path))}</div>
  </header>
  <main>
    {rows}
  </main>
</body>
</html>
"""
        tmp_path = self.viewer_path.with_suffix(".tmp.html")
        tmp_path.write_text(document, encoding=VIEWER_ENCODING)
        tmp_path.replace(self.viewer_path)


def _event_block(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", "event"))
    timestamp = str(event.get("timestamp", ""))
    body = json.dumps(event, ensure_ascii=False, indent=2, default=str)
    return (
        "<section>"
        f"<h2>{html.escape(event_name)} <span class=\"meta\">{html.escape(timestamp)}</span></h2>"
        f"<pre>{html.escape(body)}</pre>"
        "</section>"
    )


def _status_from_event(event: str, previous: str) -> str:
    if event in {"final_success"}:
        return "success"
    if event in {"final_error", "timeout"}:
        return "error"
    if event == "run_finished":
        return "finished"
    if event.startswith("interaction_poll"):
        return "polling"
    if event == "interaction_created":
        return "running"
    return previous
