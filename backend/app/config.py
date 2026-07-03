import os
from pathlib import Path
from dotenv import load_dotenv

# .env 파일은 프로젝트 루트(backend 상위)에 위치
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")

# stock_profile_ai가 쓰는 Gemini 모델. .env로 교체 가능(무료 티어 기본값).
# 무료 사용 가능 예: gemini-3.1-flash-lite(15RPM·500/일), gemini-2.5-flash, gemini-3.5-flash
GEMINI_STOCK_PROFILE_MODEL = os.getenv("GEMINI_STOCK_PROFILE_MODEL", "gemini-3.1-flash-lite")
