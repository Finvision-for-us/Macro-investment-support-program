import os
from dotenv import load_dotenv

load_dotenv()

# LLM
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
# 역할별 모델 분리
# - LITE: Scout 쿼리 생성, Planner → 최저 비용
# - FLASH: Critic(정보 충분성 판단), Synthesizer 검증 패스 → 판단력 필요
# - PRO: Synthesizer 최종 보고서 → 최고 품질
GEMINI_LITE_MODEL: str = os.getenv("GEMINI_LITE_MODEL", "gemini-2.5-flash-lite")
GEMINI_FLASH_MODEL: str = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash")
GEMINI_PRO_MODEL: str = os.getenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")

# 검색 API
PARALLEL_API_KEY: str = os.getenv("PARALLEL_API_KEY", "")

# Tavily: 쉼표로 여러 키 지원 (TAVILY_API_KEY=key1,key2,key3)
_tavily_raw = os.getenv("TAVILY_API_KEY", "")
TAVILY_API_KEYS: list[str] = [k.strip() for k in _tavily_raw.split(",") if k.strip()]
TAVILY_API_KEY: str = TAVILY_API_KEYS[0] if TAVILY_API_KEYS else ""

# 공식 데이터 소스
DART_API_KEY: str = os.getenv("DART_API_KEY", "")
FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")

# Critic grounding (google-genai 신형 SDK 사용, 기본 off)
ENABLE_CRITIC_GROUNDING: bool = os.getenv("ENABLE_CRITIC_GROUNDING", "false").lower() == "true"

# 안전 한도
MAX_SEARCH_QUERIES_PER_RUN: int = int(os.getenv("MAX_SEARCH_QUERIES_PER_RUN", "150"))
MAX_SOURCES_PER_RUN: int = int(os.getenv("MAX_SOURCES_PER_RUN", "80"))
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "5"))
MAX_RUN_SECONDS: int = int(os.getenv("MAX_RUN_SECONDS", "600"))
MAX_COST_USD_PER_RUN: float = float(os.getenv("MAX_COST_USD_PER_RUN", "2.00"))

# HTTP 설정
HTTP_TIMEOUT: float = 30.0
HTTP_CONNECT_TIMEOUT: float = 10.0
MAX_RETRIES: int = 3
RETRY_BASE_DELAY: float = 1.0

# Jina Reader (API 키 없이 사용 가능)
JINA_API_KEY: str = os.getenv("JINA_API_KEY", "")
JINA_BASE_URL: str = "https://r.jina.ai/"
JINA_RATE_LIMIT_RPM: int = 20  # 무료 티어 기준

# SEC EDGAR (API 키 불필요)
SEC_USER_AGENT: str = os.getenv("SEC_USER_AGENT", "FinVision admin@finvision.app")

# 비용 추정 (USD per 1M tokens)
LITE_INPUT_COST: float = 0.10    # gemini-2.5-flash-lite
LITE_OUTPUT_COST: float = 0.40
FLASH_INPUT_COST: float = 0.30   # gemini-2.5-flash
FLASH_OUTPUT_COST: float = 2.50
PRO_INPUT_COST: float = 1.25     # gemini-2.5-pro (200k 이하 컨텍스트)
PRO_OUTPUT_COST: float = 10.00
