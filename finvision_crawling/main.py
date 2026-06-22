"""
크롤러 메인 진입점.
1. DB 초기화
2. 채널 등록
3. 과거 메시지 일괄 수집
4. 실시간 수신 대기 (until_disconnected)
"""
import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

import config
from crawler.client import get_client
from crawler import storage
from crawler.collector import register_channel, collect_history, register_realtime_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("crawler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


async def main():
    if config.API_ID == 0:
        log.error(".env 에 TELEGRAM_API_ID 가 없습니다. setup.py 먼저 실행하세요.")
        sys.exit(1)

    if not config.CHANNELS:
        log.error("config.py 의 CHANNELS 가 비어 있습니다. 채널을 추가하세요.")
        sys.exit(1)

    await storage.init_db()
    log.info("DB 초기화 완료")

    client = get_client()
    await client.start(phone=config.PHONE)
    log.info("텔레그램 연결 완료")

    # 채널 등록
    registered = []
    for target in config.CHANNELS:
        ch = await register_channel(client, target)
        if ch:
            registered.append(ch)

    if not registered:
        log.error("유효한 채널이 없습니다.")
        await client.disconnect()
        return

    # 과거 메시지 일괄 수집
    if config.COLLECT_HISTORY:
        for ch in registered:
            await collect_history(client, ch)

    # 실시간 이벤트 등록
    channel_ids = [ch["id"] for ch in registered]
    register_realtime_handler(client, channel_ids)

    log.info("실시간 수신 대기 중... (Ctrl+C 로 종료)")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("종료됨")
