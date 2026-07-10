"""
수집 설정 파일.
채널/그룹 추가: CHANNELS 리스트에 username 또는 invite link 추가.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram API 인증 ──────────────────────────────────────────────────────────
API_ID   = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE    = os.getenv("TELEGRAM_PHONE", "")
SESSION_NAME = os.getenv("SESSION_NAME", "tg_session")

# ── 수집 대상 채널/그룹 ─────────────────────────────────────────────────────────
# username (@xxx), t.me/xxx, 또는 초대링크 모두 가능
CHANNELS: list[str] = [
    "@insidertracking",  # 미국 주식 인사이더 — 글로벌 시장 속보
]


# ── 수집 설정 ──────────────────────────────────────────────────────────────────
COLLECT_MEDIA      = False  # 텍스트 우선 수집 (미디어는 추후 활성화)
COLLECT_HISTORY    = True   # 최초 실행 시 과거 메시지 전체 수집
HISTORY_LIMIT      = 50     # 초기 확인용 (0=무제한)
MEDIA_MAX_SIZE_MB  = 100    # 이 크기 초과 파일은 메타만 저장 (바이너리 스킵)

# ── 스케줄 (실시간 수집은 listener가 담당, 폴링 보조용) ──────────────────────────
POLL_INTERVAL_SEC  = 60     # 폴링 주기 (초)

# ── 경로 ──────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(BASE_DIR, "data")
MEDIA_DIR   = os.path.join(DATA_DIR, "media")
DB_PATH     = os.path.join(DATA_DIR, "telegram.db")
SESSION_DIR = os.path.join(BASE_DIR, "sessions")
