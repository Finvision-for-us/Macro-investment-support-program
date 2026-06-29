"""M3 Day 8~9 universe 단위 테스트."""
from __future__ import annotations

import pytest

from src.universe.seeds import UNIVERSES, get_universe, universe_sectors


def test_top30_has_thirty_tickers():
    tickers = get_universe("top30")
    assert len(tickers) == 30


def test_top30_no_duplicates():
    tickers = get_universe("top30")
    assert len(set(tickers)) == 30


def test_top30_sectors_balanced():
    sectors = universe_sectors("top30")
    assert "Tech 메가" in sectors
    assert "반도체" in sectors
    assert sum(len(t) for t in sectors.values()) == 30


def test_unknown_universe_raises():
    with pytest.raises(ValueError) as exc:
        get_universe("nonexistent")
    assert "Unknown universe" in str(exc.value)


def test_universes_metadata_present():
    for name, meta in UNIVERSES.items():
        assert "description" in meta
        assert "sectors" in meta
        # 각 universe는 최소 1 종목
        total = sum(len(t) for t in meta["sectors"].values())
        assert total > 0, f"{name} has no tickers"
