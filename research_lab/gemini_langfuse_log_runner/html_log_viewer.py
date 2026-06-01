from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from schema import GeminiRunRecord, model_to_dict


RAW_PREVIEW_CHARS = 20000


def write_html_log_viewer(
    *,
    output_path: str | Path,
    record: GeminiRunRecord,
    instruction_text: str,
    prompt_text: str,
    raw_response: dict[str, Any],
) -> None:
    raw_pretty = json.dumps(raw_response, ensure_ascii=False, indent=2, default=str)
    raw_preview = raw_pretty[:RAW_PREVIEW_CHARS]
    if len(raw_pretty) > RAW_PREVIEW_CHARS:
        raw_preview += "\n\n[Preview truncated. See output/gemini_run_raw.json for the full raw response.]"

    sections = [
        ("Run Metadata", json.dumps(_metadata(record), ensure_ascii=False, indent=2)),
        ("Financial Research Instruction", instruction_text),
        ("User Prompt", prompt_text),
        ("Gemini Request Metadata", json.dumps(record.request_metadata, ensure_ascii=False, indent=2)),
        ("Events", json.dumps([model_to_dict(event) for event in record.events], ensure_ascii=False, indent=2)),
        ("Citations", json.dumps([model_to_dict(item) for item in record.citations], ensure_ascii=False, indent=2)),
        ("Final Answer", record.final_answer or "(empty)"),
        ("Raw Response Preview", raw_preview),
        ("Errors/Notes", "\n".join(record.notes) if record.notes else "(none)"),
    ]

    body_blocks = "\n".join(_section(title, content) for title, content in sections)
    copy_all_text = "\n\n".join(f"## {title}\n{content}" for title, content in sections)
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gemini Deep Research Log Viewer</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #607080;
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
      padding: 16px 22px;
      background: #ffffff;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 20px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 16px;
      overflow: hidden;
    }}
    h2 {{
      margin: 0;
      padding: 12px 14px;
      font-size: 15px;
      border-bottom: 1px solid var(--border);
      background: #fbfcfe;
    }}
    pre {{
      margin: 0;
      padding: 14px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font: 13px/1.55 Consolas, "Courier New", "Malgun Gothic", monospace;
    }}
    button {{
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
      font-size: 13px;
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Gemini Deep Research Log Viewer</h1>
      <div class="meta">Drag any block to copy. Copy All is optional.</div>
    </div>
    <button type="button" onclick="copyAll()">Copy All</button>
  </header>
  <main>
    {body_blocks}
  </main>
  <script>
    const COPY_ALL_TEXT = {json.dumps(copy_all_text, ensure_ascii=False)};
    async function copyAll() {{
      try {{
        await navigator.clipboard.writeText(COPY_ALL_TEXT);
        alert("Copied all log text.");
      }} catch (error) {{
        alert("Copy failed. Please drag-select the log blocks manually.");
      }}
    }}
  </script>
</body>
</html>
"""
    Path(output_path).write_text(document, encoding="utf-8")


def _section(title: str, content: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2><pre>{html.escape(content)}</pre></section>"


def _metadata(record: GeminiRunRecord) -> dict[str, Any]:
    data = model_to_dict(record)
    data.pop("events", None)
    data.pop("citations", None)
    data.pop("final_answer", None)
    return data
