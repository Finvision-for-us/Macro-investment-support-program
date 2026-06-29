"""정규화 저장 — SQLite.

item_id(=source_id:source_native_id)를 PRIMARY KEY로 두고 INSERT OR IGNORE 하여
재수집 시 같은 레코드를 자동으로 무시(값싼 중복 차단). 조회·정렬용으로 핵심 컬럼을
풀어두고, 전체 NewsItem은 payload(JSON)로 보관해 스키마 진화에 견디게 한다.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from ..schema import NewsItem

DEFAULT_DB_PATH = Path("data/ingest2/news.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    item_id          TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    source_native_id TEXT NOT NULL,
    trust_tier       INTEGER NOT NULL,
    published_at     TEXT,
    collected_at     TEXT NOT NULL,
    filter_status    TEXT NOT NULL,
    payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_source ON news_items(source_id);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at);
"""


class NewsStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def save(self, item: NewsItem) -> bool:
        """삽입 성공=True, 재수집 중복으로 무시=False."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO news_items
               (item_id, source_id, source_native_id, trust_tier,
                published_at, collected_at, filter_status, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.item_id,
                item.source_id,
                item.source_native_id,
                item.trust_tier,
                item.published_at.isoformat() if item.published_at else None,
                item.collected_at.isoformat(),
                item.filter_status,
                item.model_dump_json(),
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get(self, item_id: str) -> NewsItem | None:
        row = self.conn.execute(
            "SELECT payload FROM news_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        return NewsItem.model_validate_json(row["payload"]) if row else None

    def set_filter(
        self, item_id: str, status: str, reasons: list[str], flags: list[str]
    ) -> bool:
        """1차 필터 결과를 반영(filter_status 컬럼 + payload 동기화)."""
        item = self.get(item_id)
        if item is None:
            return False
        item.filter_status = status  # type: ignore[assignment]
        item.rejected_reasons = reasons
        item.flags = flags
        self.conn.execute(
            "UPDATE news_items SET filter_status = ?, payload = ? WHERE item_id = ?",
            (status, item.model_dump_json(), item_id),
        )
        self.conn.commit()
        return True

    def update(self, item: NewsItem) -> bool:
        """변경된 NewsItem 전체를 payload에 다시 반영(분류 결과 저장 등)."""
        cur = self.conn.execute(
            "UPDATE news_items SET filter_status = ?, payload = ? WHERE item_id = ?",
            (item.filter_status, item.model_dump_json(), item.item_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def iter_items(self, source_id: str | None = None) -> Iterator[NewsItem]:
        if source_id:
            rows = self.conn.execute(
                "SELECT payload FROM news_items WHERE source_id = ?", (source_id,)
            )
        else:
            rows = self.conn.execute("SELECT payload FROM news_items")
        for row in rows:
            yield NewsItem.model_validate_json(row["payload"])

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
