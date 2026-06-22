"""
Telethon 클라이언트 싱글톤.
세션 파일은 sessions/ 디렉토리에 저장됨.
"""
import os
from telethon import TelegramClient
import config

_client: TelegramClient | None = None


def get_client() -> TelegramClient:
    global _client
    if _client is None:
        os.makedirs(config.SESSION_DIR, exist_ok=True)
        session_path = os.path.join(config.SESSION_DIR, config.SESSION_NAME)
        _client = TelegramClient(
            session_path,
            config.API_ID,
            config.API_HASH,
            # 연결 안정성 옵션
            connection_retries=10,
            retry_delay=5,
            auto_reconnect=True,
            receive_updates=True,
        )
    return _client
