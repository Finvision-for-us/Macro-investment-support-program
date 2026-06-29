"""Polygon.io 티커별 뉴스 수집기."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from polygon import RESTClient
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import POLYGON_API_KEY
from src.ingest.schema import RawNews


class MissingAPIKeyError(RuntimeError):
    """설정 누락 — retry 대상에서 제외."""


def _client() -> RESTClient:
    if not POLYGON_API_KEY:
        raise MissingAPIKeyError(
            "POLYGON_API_KEY not set. Copy .env.example to .env and fill in your key."
        )
    return RESTClient(POLYGON_API_KEY)


def fetch_news(ticker: str, days: int = 7, limit_per_page: int = 1000) -> list[RawNews]:
    """지정 종목의 최근 ``days``일치 뉴스를 ``RawNews`` 리스트로 반환."""
    client = _client()  # 키 누락은 즉시 실패 (retry 안 함)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return _fetch_with_retry(client, ticker, since, limit_per_page)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_with_retry(
    client: RESTClient, ticker: str, since: datetime, limit_per_page: int
) -> list[RawNews]:
    results: list[RawNews] = []
    for article in client.list_ticker_news(
        ticker=ticker,
        published_utc_gte=since.isoformat(),
        order="desc",
        limit=limit_per_page,
        sort="published_utc",
    ):
        try:
            results.append(_to_raw_news(article))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] skip article: {e}")
            continue
    return results


def _to_raw_news(article: Any) -> RawNews:
    """Polygon SDK 응답을 RawNews로 정규화."""
    published = article.published_utc
    if isinstance(published, str):
        published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
    else:
        published_at = published

    publisher_name = ""
    if getattr(article, "publisher", None) is not None:
        publisher_name = getattr(article.publisher, "name", "") or ""

    return RawNews(
        id=article.id,
        title=article.title or "",
        description=getattr(article, "description", "") or "",
        published_at=published_at,
        url=article.article_url,
        publisher=publisher_name,
        tickers=list(getattr(article, "tickers", None) or []),
        keywords=list(getattr(article, "keywords", None) or []),
    )
