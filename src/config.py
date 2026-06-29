"""환경 변수 및 전역 상수."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
POLYGON_API_KEY: str = os.environ.get("POLYGON_API_KEY", "")
TAVILY_API_KEY: str = os.environ.get("TAVILY_API_KEY", "")
PARALLEL_API_KEY: str = os.environ.get("PARALLEL_API_KEY", "")
FRED_API_KEY: str = os.environ.get("FRED_API_KEY", "")  # M3.5: 거시지표 수집

OUTPUTS_DIR = ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

DB_PATH = ROOT / "finvision.db"

CLUSTER_SIMILARITY_THRESHOLD = 0.82
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768  # Matryoshka: 3072 -> 768 (메모리 절감, 품질 영향 미미)
GEMINI_MODEL_FAST = "gemini-3.1-flash-lite"
GEMINI_MODEL_DEEP = "gemini-3.1-flash-lite"  # 결제 활성 후 'gemini-2.5-pro' 등으로 복원 검토
