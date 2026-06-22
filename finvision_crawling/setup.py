# -*- coding: utf-8 -*-
"""
최초 실행 시 1회만 실행 -> 세션 파일 생성.
이후엔 main.py만 실행하면 됨.

실행:
  python setup.py
"""
import asyncio
import sys
import os

from dotenv import load_dotenv
load_dotenv()

import config
from crawler.client import get_client

CODE_FILE = os.path.join(os.path.dirname(__file__), ".tg_code")


def _read_code():
    """인증코드를 .tg_code 파일에서 읽거나, 없으면 직접 입력받음."""
    if os.path.exists(CODE_FILE):
        with open(CODE_FILE, "r") as f:
            code = f.read().strip()
        os.remove(CODE_FILE)
        print(f"[코드] {code} (파일에서 로드)")
        return code
    return input("인증코드 입력: ").strip()


async def main():
    if config.API_ID == 0 or not config.API_HASH:
        print("[ERROR] .env 파일에 TELEGRAM_API_ID / TELEGRAM_API_HASH 를 입력하세요.")
        sys.exit(1)

    phone = config.PHONE or input("전화번호 입력 (예: +821012345678): ").strip()
    print(f"전화번호: {phone}")

    client = get_client()
    print("\n텔레그램 로그인 중...")

    await client.start(phone=phone, code_callback=_read_code)

    me = await client.get_me()
    print(f"\n[OK] 로그인 성공!")
    print(f"   이름     : {me.first_name} {me.last_name or ''}")
    print(f"   username : @{me.username}")
    print(f"   phone    : {me.phone}")
    print(f"   세션파일  : sessions/{config.SESSION_NAME}.session")
    print("\n이제 main.py 를 실행하세요.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
