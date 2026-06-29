"""수집 레이어 데이터 스키마."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawNews(BaseModel):
    """원본 뉴스 1건 (수집 직후, 정규화만 된 상태)."""

    id: str
    title: str
    description: str = ""
    published_at: datetime
    url: str
    publisher: str = ""
    tickers: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source: str = "polygon"


class Event(BaseModel):
    """동일 사건으로 클러스터링된 뉴스 묶음 = 1개 이벤트."""

    id: str
    title: str
    summary: str
    occurred_at: datetime
    source_urls: list[str]
    publishers: list[str]
    tickers_mentioned: list[str]
    spread: int

    @classmethod
    def from_cluster(cls, cluster_id: str, items: list[RawNews], summary: str) -> Event:
        sorted_items = sorted(items, key=lambda n: n.published_at)
        rep = sorted_items[0]
        return cls(
            id=cluster_id,
            title=rep.title,
            summary=summary,
            occurred_at=rep.published_at,
            source_urls=[n.url for n in sorted_items],
            publishers=sorted({n.publisher for n in sorted_items if n.publisher}),
            tickers_mentioned=sorted({t for n in sorted_items for t in n.tickers}),
            spread=len(sorted_items),
        )
