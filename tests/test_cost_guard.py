"""M3 Day 12~13 cost_guard 단위 테스트."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src import cost_guard


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(cost_guard, "DB_PATH", db)
    return db


def test_log_call_creates_row(tmp_db):
    cost_guard.log_call("parallel", "search:basic", count=2)
    with sqlite3.connect(tmp_db) as c:
        rows = c.execute("SELECT provider, endpoint, count, cost_usd FROM api_calls").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "parallel"
    assert rows[0][1] == "search:basic"
    assert rows[0][2] == 2
    # cost = 0.015 * 2 = 0.030
    assert abs(rows[0][3] - 0.030) < 1e-6


def test_log_call_explicit_cost_overrides_default(tmp_db):
    cost_guard.log_call("custom", "endpoint", cost_usd=0.99)
    assert cost_guard.parallel_used_usd() == 0.0
    summary = cost_guard.usage_summary()
    assert any(r["cost_usd"] == 0.99 for r in summary)


def test_parallel_remaining_decreases_with_use(tmp_db):
    initial_rem = cost_guard.parallel_remaining_usd(initial=5.0)
    assert initial_rem == 5.0
    # 1 search:advanced (0.04) + 1 extract (0.05) = 0.09
    cost_guard.log_call("parallel", "search:advanced")
    cost_guard.log_call("parallel", "extract")
    assert abs(cost_guard.parallel_remaining_usd(initial=5.0) - 4.91) < 1e-6


def test_should_skip_deep_when_below_block_pct(tmp_db):
    # use 4.8 / 5.0 = 96% — remaining 4%, < 5% block threshold
    cost_guard.log_call("parallel", "extract", count=96)  # 0.05 × 96 = 4.8
    assert cost_guard.should_skip_deep(initial=5.0)


def test_no_skip_when_plenty_remaining(tmp_db):
    cost_guard.log_call("parallel", "search:basic", count=1)
    assert not cost_guard.should_skip_deep(initial=5.0)


def test_warn_message_tiers(tmp_db):
    # plenty
    cost_guard.log_call("parallel", "search:basic", count=1)
    assert cost_guard.warn_message(initial=5.0) is None
    # warning tier — 81% used
    cost_guard.log_call("parallel", "extract", count=80)  # ~4
    msg = cost_guard.warn_message(initial=5.0)
    assert msg is not None and "곧 소진" in msg


def test_usage_summary_filters_by_days(tmp_db):
    # 옛 호출 1건 + 최근 호출 1건
    cost_guard._ensure_table()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with sqlite3.connect(tmp_db) as c:
        c.execute(
            "INSERT INTO api_calls (timestamp, provider, endpoint, cost_usd, count) "
            "VALUES (?, ?, ?, ?, ?)",
            (old, "parallel", "search:basic", 0.015, 1),
        )
    cost_guard.log_call("parallel", "search:basic")
    summary = cost_guard.usage_summary(days=30)
    # 최근 1건만 보임
    parallel_rows = [r for r in summary if r["provider"] == "parallel"]
    assert sum(r["calls"] for r in parallel_rows) == 1
