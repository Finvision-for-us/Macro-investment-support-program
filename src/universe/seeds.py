"""시드 종목 universe 리스트 (PROJECT_SPEC §11.3)."""
from __future__ import annotations

UNIVERSES: dict[str, dict] = {
    "top30": {
        "description": "Sector-balanced top 30 by market cap",
        "sectors": {
            "Tech 메가": ["NVDA", "MSFT", "GOOGL", "AAPL", "META", "AMZN", "TSLA"],
            "반도체": ["AVGO", "AMD", "INTC", "MU", "QCOM", "TSM"],
            "금융": ["JPM", "BAC", "V", "MA", "BRK.B"],
            "헬스케어": ["LLY", "UNH", "JNJ", "PFE"],
            "소비재": ["WMT", "COST", "HD", "PG", "KO"],
            "에너지/산업": ["XOM", "CVX", "BA"],
        },
    },
}


def _resolve(name: str) -> str:
    """대소문자 무관 lookup. 매칭 안 되면 ValueError."""
    key = name.lower()
    for k in UNIVERSES:
        if k.lower() == key:
            return k
    available = ", ".join(UNIVERSES.keys())
    raise ValueError(f"Unknown universe '{name}'. Available: {available}")


def get_universe(name: str) -> list[str]:
    """universe 이름 → ticker 리스트 (섹터 순). 대소문자 무관."""
    real = _resolve(name)
    tickers: list[str] = []
    for sec_tickers in UNIVERSES[real]["sectors"].values():
        tickers.extend(sec_tickers)
    return tickers


def universe_sectors(name: str) -> dict[str, list[str]]:
    """universe 이름 → {섹터명: tickers}. 대소문자 무관."""
    real = _resolve(name)
    return UNIVERSES[real]["sectors"]
