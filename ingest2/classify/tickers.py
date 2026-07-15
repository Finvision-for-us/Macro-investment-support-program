"""티커 사전 — SEC company_tickers.json(~1만 미국 상장사) 기반.

- CIK→티커: SEC 기사에 정확 매핑(공짜).
- 텍스트→티커: **고정밀만**. ① $TICKER/(TICKER) 명시 심볼 ② 핵심 대형주 별칭 사전.
  자유 텍스트의 long-tail 종목·간접 티커는 오탐이 커서 향후 Gemini 단계로 미룬다.
  (이전의 '회사명 첫 토큰' 휴리스틱은 'Federal Reserve'→RSRV 같은 오탐이 심해 제거)
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from collections.abc import Iterable
from pathlib import Path

DEFAULT_CACHE = Path("data/ingest2/company_tickers.json")
_SRC_URL = "https://www.sec.gov/files/company_tickers.json"
_UA = "finvision/0.1 ingest2 (contact: pqqpqqpqqpqq7663@gmail.com)"
_CACHE_MAX_AGE = 7 * 24 * 3600  # 7일

# 핵심 대형주 별칭(소문자) → 티커. 설계가 주목하는 종목 위주, 고정밀.
ALIASES: dict[str, str] = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "amazon": "AMZN",
    "alphabet": "GOOGL", "google": "GOOGL", "meta": "META", "facebook": "META",
    "tesla": "TSLA", "micron": "MU", "advanced micro devices": "AMD",
    "broadcom": "AVGO", "taiwan semiconductor": "TSM", "tsmc": "TSM", "intel": "INTC",
    "qualcomm": "QCOM", "netflix": "NFLX", "oracle": "ORCL", "salesforce": "CRM",
    "adobe": "ADBE", "palantir": "PLTR", "super micro": "SMCI", "supermicro": "SMCI",
    "vertiv": "VRT", "ibm": "IBM", "boeing": "BA", "jpmorgan": "JPM", "jp morgan": "JPM",
    "bank of america": "BAC", "walmart": "WMT", "disney": "DIS", "paypal": "PYPL",
    "coinbase": "COIN", "microstrategy": "MSTR", "general motors": "GM", "ford": "F",
    "coca-cola": "KO", "coca cola": "KO", "exxon": "XOM", "chevron": "CVX",
}
_SYMBOL_RE = re.compile(r"(?:\$([A-Z]{1,5})\b|\(([A-Z]{1,5})\))")
_ALIAS_RE = re.compile(
    r"\b("
    + "|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))
    + r")\b(?!-)"  # 'meta-analysis' 같은 하이픈 합성어의 부분 매치 방지
)

# 영어 상용어/약어와 철자가 겹치는 실존 티커.
# 뉴스 본문의 "(AI)", "(IT)" 같은 괄호 약어가 C3.ai(AI)·Gartner(IT) 직접티커로
# 오탐되는 것을 막는다. 이 티커들은 `$AI`처럼 달러 프리픽스일 때만 인정.
_COMMON_WORD_TICKERS = frozenset({
    "AI", "IT", "ALL", "ON", "NOW", "KEY", "SO", "GO", "BIG", "COST",
    "A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "O",
    "R", "S", "T", "U", "V", "W", "X", "Y", "Z",   # 1글자 티커 전부
    "AN", "AT", "BE", "BY", "DO", "EV", "HE", "IP", "MA", "ME", "MY",
    "OR", "PC", "SEE", "TWO", "WELL", "GOOD", "REAL", "PLAY", "OPEN",
    "LOVE", "FAST", "CASH", "NICE", "SAFE", "PLUS", "MAIN", "FUND",
    "CAR", "CARS", "FUN", "LOW", "PAY", "RUN", "EAT", "NEW", "OLD",
    "OUT", "TOP", "UP", "VS", "WIN", "YOU", "ANY", "ARE", "CAN",
    "HAS", "ONE", "PRO", "SUB", "TV",
})


class TickerMap:
    def __init__(self, cik_to_ticker: dict, ticker_to_name: dict) -> None:
        self.cik_to_ticker = cik_to_ticker
        self.ticker_to_name = ticker_to_name
        self.tickers_set = set(ticker_to_name)

    @classmethod
    def from_rows(cls, rows: Iterable[dict]) -> TickerMap:
        cik_to_ticker: dict[int, str] = {}
        ticker_to_name: dict[str, str] = {}
        for r in rows:
            tk = (r.get("ticker") or "").upper()
            if not tk:
                continue
            cik_to_ticker[int(r["cik_str"])] = tk
            ticker_to_name.setdefault(tk, r.get("title") or "")
        return cls(cik_to_ticker, ticker_to_name)

    @classmethod
    def from_sec(cls, cache_path: Path | str = DEFAULT_CACHE, user_agent: str = _UA) -> TickerMap:
        cache_path = Path(cache_path)
        fresh = (
            cache_path.exists()
            and (time.time() - cache_path.stat().st_mtime) < _CACHE_MAX_AGE
        )
        if not fresh:
            req = urllib.request.Request(_SRC_URL, headers={"User-Agent": user_agent})
            data = urllib.request.urlopen(req, timeout=30).read()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return cls.from_rows(raw.values())

    def for_cik(self, cik) -> str | None:
        try:
            return self.cik_to_ticker.get(int(cik))
        except (TypeError, ValueError):
            return None

    def find_in_text(self, text: str) -> list[str]:
        found: list[str] = []
        for m in _SYMBOL_RE.finditer(text):           # $NVDA / (NVDA)
            sym = m.group(1) or m.group(2)
            # 괄호형 "(AI)"/"(IT)"는 상용어 약어일 확률이 높아 상용어 충돌 티커는
            # $ 프리픽스(m.group(1))일 때만 인정한다.
            if m.group(2) and sym in _COMMON_WORD_TICKERS:
                continue
            if sym in self.tickers_set and sym not in found:
                found.append(sym)
        for m in _ALIAS_RE.finditer(text.lower()):     # 핵심 대형주 별칭
            tk = ALIASES[m.group(1)]
            if tk not in found:
                found.append(tk)
        return found

    def names_for(self, tickers: Iterable[str]) -> list[str]:
        return [self.ticker_to_name[t] for t in tickers if t in self.ticker_to_name]
