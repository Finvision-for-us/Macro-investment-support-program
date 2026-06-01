from __future__ import annotations

import argparse
from pathlib import Path

from comparator import compare_traces
from parsers import parse_finvision_log, parse_gemini_log, parse_openai_log


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare copied Deep Research logs locally.")
    parser.add_argument("--gemini", help="Path to a Gemini Deep Research text log.")
    parser.add_argument("--openai", help="Path to an OpenAI/ChatGPT Deep Research JSON or text log.")
    parser.add_argument("--finvision", help="Path to a FinVision Deep Research JSON or text log.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for comparison outputs.")
    args = parser.parse_args()

    traces = []
    if args.gemini:
        traces.append(parse_gemini_log(args.gemini))
    if args.openai:
        traces.append(parse_openai_log(args.openai))
    if args.finvision:
        traces.append(parse_finvision_log(args.finvision))

    if not traces:
        traces = [
            parse_gemini_log(str(BASE_DIR / "input" / "gemini_log_sample.txt")),
            parse_openai_log(str(BASE_DIR / "input" / "openai_log_sample.json")),
            parse_finvision_log(str(BASE_DIR / "input" / "finvision_log_sample.json")),
        ]

    compare_traces(traces, args.output_dir)
    print(f"Wrote comparison outputs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
