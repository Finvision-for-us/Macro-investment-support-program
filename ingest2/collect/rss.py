"""RSS 수집 어댑터 (trust_tier=3).

피드 1개당 RssCollector 인스턴스 1개. source_id를 피드별로 부여해 원본 저장과
item_id가 피드 단위로 분리·유니크하게 유지된다. 시작 피드(D8)는 FEEDS에 정의하며,
추가/교체는 이 리스트 한 줄로 끝난다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser

from ..schema import NewsItem, RawRecord
from .base import BaseCollector


@dataclass(frozen=True)
class FeedConfig:
    source_id: str
    feed_url: str
    source_name: str


# D8: 시작 피드 — 미국 시장 광역 2종 (실측으로 내용 확인). 교체/추가는 여기서.
FEEDS: list[FeedConfig] = [
    FeedConfig(
        "rss_cnbc_finance",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "CNBC Finance",
    ),
    FeedConfig(
        "rss_marketwatch_bulletins",
        "http://feeds.marketwatch.com/marketwatch/bulletins/",
        "MarketWatch Bulletins",
    ),
]


def _native_id(entry) -> str:
    return entry.get("id") or entry.get("guid") or entry.get("link") or ""


def _parsed_dt(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=UTC) if t else None


def _entry_to_dict(entry) -> dict:
    """원본 보관용 JSON-safe dict (struct_time → ISO)."""
    dt = _parsed_dt(entry)
    return {
        "id": _native_id(entry),
        "title": entry.get("title", ""),
        "summary": entry.get("summary", ""),
        "link": entry.get("link", ""),
        "author": entry.get("author", ""),
        "published": dt.isoformat() if dt else None,
        "tags": [t.get("term", "") for t in entry.get("tags", [])],
    }


class RssCollector(BaseCollector):
    trust_tier = 3

    def __init__(self, config: FeedConfig) -> None:
        self.config = config
        self.source_id = config.source_id

    def fetch(self, since: datetime, until: datetime) -> list[RawRecord]:
        now = datetime.now(UTC)
        parsed = feedparser.parse(self.config.feed_url)
        out: list[RawRecord] = []
        for entry in parsed.entries:
            native_id = _native_id(entry)
            if not native_id:
                continue
            dt = _parsed_dt(entry)
            # 발행시간 있으면 [since, until) 필터, 없으면 포함(1차 필터가 처리)
            if dt is not None and not (since <= dt < until):
                continue
            out.append(
                RawRecord(
                    source_id=self.source_id,
                    source_native_id=native_id,
                    content_type="json",
                    payload=json.dumps(_entry_to_dict(entry), ensure_ascii=False),
                    url=entry.get("link"),
                    fetched_at=now,
                )
            )
        return out

    def normalize(self, raw: RawRecord) -> NewsItem:
        d = json.loads(raw.payload)
        published = datetime.fromisoformat(d["published"]) if d.get("published") else None
        return NewsItem(
            item_id=self.make_item_id(raw.source_id, raw.source_native_id),
            source_id=raw.source_id,
            source_native_id=raw.source_native_id,
            trust_tier=self.trust_tier,
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            url=d.get("link") or (raw.url or ""),
            canonical_url=d.get("link") or raw.url,
            source_name=self.config.source_name,
            author=d.get("author", ""),
            published_at=published,
            collected_at=raw.fetched_at,
            language="en",
            raw_category=",".join(d.get("tags", [])),
        )


def default_collectors() -> list[RssCollector]:
    """FEEDS에 정의된 모든 피드의 수집기."""
    return [RssCollector(fc) for fc in FEEDS]
