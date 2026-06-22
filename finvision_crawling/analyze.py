# -*- coding: utf-8 -*-
"""
수집된 포스트를 Gemini로 분석하는 실행 스크립트.

실행:
  python analyze.py              # 전체 미분석 포스트 분석
  python analyze.py --limit 5   # 최근 5개만
"""
import asyncio
import argparse
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from crawler import storage
from crawler.analyzer import run_analysis, DEFAULT_PROMPT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("analyzer.log", encoding="utf-8"),
    ],
)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", type=int, default=None, help="특정 채널 ID만 분석")
    parser.add_argument("--delay",   type=float, default=2.0, help="포스트 간 딜레이(초)")
    args = parser.parse_args()

    await storage.init_db()
    await run_analysis(
        channel_id=args.channel,
        prompt=DEFAULT_PROMPT,
        delay_sec=args.delay,
    )


if __name__ == "__main__":
    asyncio.run(main())
