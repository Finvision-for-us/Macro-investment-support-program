from __future__ import annotations

import argparse
import sys

from langfuse_client import LangfuseConfigError
from parsers import parse_finvision_log, parse_gemini_log, parse_openai_log
from trace_uploader import upload_research_trace


PARSERS = {
    "gemini": parse_gemini_log,
    "openai": parse_openai_log,
    "finvision": parse_finvision_log,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a Deep Research log as a Langfuse trace.")
    parser.add_argument("--type", required=True, choices=sorted(PARSERS), help="Log type to parse.")
    parser.add_argument("--file", required=True, help="Path to the copied research log file.")
    args = parser.parse_args()

    trace = PARSERS[args.type](args.file)
    try:
        trace_id = upload_research_trace(trace)
    except LangfuseConfigError as exc:
        print(f"Upload skipped: {exc}", file=sys.stderr)
        return 2

    print(f"Uploaded trace for {trace.engine_name}. trace_id={trace_id or '(unknown)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
