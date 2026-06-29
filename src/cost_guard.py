"""API 호출 누적 + Parallel 잔여 크레딧 가드.

SQLite api_calls 테이블에 매 호출 누적, 잔여 크레딧 < 20% 경고, < 5% deep skip.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.config import DB_PATH

# Parallel.ai 무료 크레딧 (대략) — 사용자 환경에 따라 조정 필요
PARALLEL_INITIAL_CREDIT_USD = 5.0
PARALLEL_WARN_PCT = 0.20
PARALLEL_BLOCK_PCT = 0.05

# 호출당 추정 비용 (USD)
PROVIDER_COSTS: dict[str, float] = {
    "parallel:search:basic": 0.015,
    "parallel:search:advanced": 0.040,
    "parallel:extract": 0.050,
    "tavily:search": 0.0,
    "gemini:generate": 0.001,
    "gemini:embed": 0.0001,
    "polygon:news": 0.0,
    "yfinance:history": 0.0,
}


def _ensure_table() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                provider TEXT NOT NULL,
                endpoint TEXT,
                cost_usd REAL DEFAULT 0,
                count INTEGER DEFAULT 1,
                notes TEXT
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_calls_provider ON api_calls(provider, timestamp)"
        )


def log_call(
    provider: str,
    endpoint: str = "",
    count: int = 1,
    cost_usd: float | None = None,
    notes: str = "",
) -> None:
    """단일 API 호출 기록. cost 미지정 시 PROVIDER_COSTS에서 추정."""
    try:
        _ensure_table()
        key = f"{provider}:{endpoint}" if endpoint else provider
        if cost_usd is None:
            cost_usd = PROVIDER_COSTS.get(key, 0.0) * count
        with sqlite3.connect(DB_PATH) as c:
            c.execute(
                "INSERT INTO api_calls "
                "(timestamp, provider, endpoint, cost_usd, count, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    provider,
                    endpoint,
                    cost_usd,
                    count,
                    notes,
                ),
            )
    except Exception:
        # 로깅 실패가 본 작업 깨면 안 됨
        pass


def usage_summary(days: int = 30) -> list[dict]:
    """최근 N일 사용량 그룹 요약."""
    _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            """
            SELECT provider, endpoint,
                   SUM(count) AS calls,
                   ROUND(SUM(cost_usd), 4) AS cost,
                   MIN(timestamp) AS first_at,
                   MAX(timestamp) AS last_at
            FROM api_calls WHERE timestamp >= ?
            GROUP BY provider, endpoint
            ORDER BY cost DESC, calls DESC
            """,
            (cutoff,),
        ).fetchall()
    return [
        {
            "provider": r[0],
            "endpoint": r[1] or "",
            "calls": int(r[2] or 0),
            "cost_usd": float(r[3] or 0.0),
            "first_at": r[4],
            "last_at": r[5],
        }
        for r in rows
    ]


def parallel_used_usd() -> float:
    _ensure_table()
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_calls WHERE provider = 'parallel'"
        ).fetchone()
    return float(row[0])


def parallel_remaining_usd(initial: float = PARALLEL_INITIAL_CREDIT_USD) -> float:
    return max(0.0, initial - parallel_used_usd())


def parallel_remaining_pct(initial: float = PARALLEL_INITIAL_CREDIT_USD) -> float:
    if initial <= 0:
        return 0.0
    return parallel_remaining_usd(initial) / initial


def should_skip_deep(initial: float = PARALLEL_INITIAL_CREDIT_USD) -> bool:
    return parallel_remaining_pct(initial) < PARALLEL_BLOCK_PCT


def warn_message(initial: float = PARALLEL_INITIAL_CREDIT_USD) -> str | None:
    """잔여 크레딧 경고 (없으면 None)."""
    pct = parallel_remaining_pct(initial)
    rem = parallel_remaining_usd(initial)
    if pct < PARALLEL_BLOCK_PCT:
        return (
            f"Parallel 잔여 ${rem:.2f}/{initial:.2f} ({pct * 100:.1f}%) — "
            f"deep research 자동 skip 권장"
        )
    if pct < PARALLEL_WARN_PCT:
        return (
            f"Parallel 잔여 ${rem:.2f}/{initial:.2f} ({pct * 100:.1f}%) — 곧 소진"
        )
    return None
