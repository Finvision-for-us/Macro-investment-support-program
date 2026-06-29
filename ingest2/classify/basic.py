"""경량 분류 — NewsItem에 companies / tickers_direct / event_type 부착.

SEC: source_meta의 cik로 직접티커(정확). RSS: 텍스트에서 보수적 티커매칭 + 이벤트 키워드.
간접 티커(파급 종목)는 채우지 않는다 — 추론이 필요해 향후 Gemini 단계 몫.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..schema import NewsItem
from .events import event_for_text
from .tickers import TickerMap


@dataclass
class ClassifyResult:
    companies: list[str] = field(default_factory=list)
    tickers_direct: list[str] = field(default_factory=list)
    event_type: str | None = None


def _confirmed_existing_tickers(item: NewsItem, ticker_map: TickerMap, text: str) -> list[str]:
    """API 제공 티커 보존.

    다만 1글자 티커(M/F/T/C 등)는 일반 단어·괄호 오탐이 잦으므로 회사명이나 명시
    심볼이 텍스트에 확인될 때만 보존한다.
    """
    confirmed: list[str] = []
    lower = text.lower()
    explicit = set(ticker_map.find_in_text(text))
    for ticker in item.tickers_direct:
        ticker = ticker.upper()
        if len(ticker) > 1 or ticker in explicit:
            confirmed.append(ticker)
            continue
        company = ticker_map.ticker_to_name.get(ticker, "").lower()
        company_tokens = [p for p in company.replace(",", " ").split() if len(p) > 3]
        if company_tokens and any(token in lower for token in company_tokens):
            confirmed.append(ticker)
    return list(dict.fromkeys(confirmed))


def classify(item: NewsItem, ticker_map: TickerMap) -> ClassifyResult:
    if item.source_id == "sec_edgar":
        tk = ticker_map.for_cik(item.source_meta.get("cik", ""))
        return ClassifyResult(
            companies=item.companies,                     # SEC normalize에서 이미 채움
            tickers_direct=[tk] if tk else [],
            event_type=item.event_type,                   # 폼타입 매핑 유지
        )
    text = f"{item.title} {item.summary}"
    found = ticker_map.find_in_text(text)
    tickers = list(dict.fromkeys([*_confirmed_existing_tickers(item, ticker_map, text), *found]))
    return ClassifyResult(
        companies=ticker_map.names_for(tickers) or item.companies,
        tickers_direct=tickers,
        event_type=event_for_text(text) or item.event_type,
    )


def run_classify(news_store, ticker_map: TickerMap) -> dict:
    """필터를 통과한 항목만 분류해 store에 반영. 분포 통계 반환."""
    stats = {"classified": 0, "with_ticker": 0, "with_event": 0, "events": Counter()}
    for item in list(news_store.iter_items()):
        if item.filter_status != "passed":
            continue
        res = classify(item, ticker_map)
        item.companies = res.companies
        item.tickers_direct = res.tickers_direct
        item.event_type = res.event_type  # type: ignore[assignment]
        news_store.update(item)
        stats["classified"] += 1
        if res.tickers_direct:
            stats["with_ticker"] += 1
        if res.event_type:
            stats["with_event"] += 1
            stats["events"][res.event_type] += 1
    return stats
