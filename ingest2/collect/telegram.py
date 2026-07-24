"""텔레그램 채널 어댑터 (trust_tier=5).

finvision_crawling/data/telegram.db 를 읽어 NewsItem 으로 변환한다.
채널 목록은 _CHANNELS 에 정의. 추가는 이 리스트 한 줄로 끝난다.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime

from ..schema import NewsItem, RawRecord
from .base import BaseCollector

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_DB = os.path.join(
    _PROJECT_ROOT, "finvision_crawling", "data", "telegram.db"
)

_MIN_TEXT = 30  # 이보다 짧은 메시지는 스킵 (환율 틱, 이모지 단독 등)
_BOLD_RE = re.compile(r"\*{1,2}")


def _extract_title(text: str) -> str:
    """첫 비어있지 않은 줄을 제목으로 (최대 120자). 마크다운 볼드 제거."""
    for line in text.splitlines():
        line = _BOLD_RE.sub("", line).strip()
        if line:
            return line[:120]
    return text[:120]


def _first_url(entities: list[dict]) -> str | None:
    for e in entities:
        if e.get("type") == "url" and e.get("value"):
            val = e["value"]
            # http(s):// 로 시작하는 유효 URL만, t.me 내부링크는 제외
            if val.startswith(("http://", "https://")) and "t.me" not in val:
                return val
    return None


def _permalink(username: str | None, channel_id: int, msg_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{msg_id}"
    return f"https://t.me/c/{channel_id}/{msg_id}"


class TelegramCollector(BaseCollector):
    trust_tier = 5

    def __init__(
        self,
        channel_id: int,
        source_id: str,
        source_name: str,
        channel_username: str | None = None,
        db_path: str = _DEFAULT_DB,
    ) -> None:
        self.channel_id = channel_id
        self.source_id = source_id
        self.source_name = source_name
        self.channel_username = channel_username
        self.db_path = db_path

    def fetch(self, since: datetime, until: datetime) -> list[RawRecord]:
        if not os.path.exists(self.db_path):
            return []

        # SQLite 날짜 비교: 저장 형식이 +00:00 UTC ISO 문자열
        since_s = since.astimezone(UTC).isoformat()
        until_s = until.astimezone(UTC).isoformat()

        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            cur = con.cursor()
            rows = cur.execute(
                """
                SELECT id, post_id, date, text
                FROM   messages
                WHERE  channel_id = ?
                  AND  text IS NOT NULL AND text != ''
                  AND  date >= ? AND date < ?
                ORDER  BY date ASC
                """,
                (self.channel_id, since_s, until_s),
            ).fetchall()

            seen_posts: set[int] = set()
            now = datetime.now(UTC)
            out: list[RawRecord] = []

            for row in rows:
                pid = row["post_id"]
                if pid in seen_posts:
                    continue
                seen_posts.add(pid)

                text = (row["text"] or "").strip()
                if len(text) < _MIN_TEXT:
                    continue

                ents = cur.execute(
                    "SELECT type, value FROM entities WHERE post_id=? AND channel_id=?",
                    (pid, self.channel_id),
                ).fetchall()

                payload = json.dumps(
                    {
                        "msg_id":          row["id"],
                        "post_id":         pid,
                        "date":            row["date"],
                        "text":            text,
                        "entities":        [dict(e) for e in ents],
                        "channel_id":      self.channel_id,
                        "channel_username": self.channel_username,
                    },
                    ensure_ascii=False,
                )
                out.append(
                    RawRecord(
                        source_id=self.source_id,
                        source_native_id=str(pid),
                        content_type="json",
                        payload=payload,
                        url=_permalink(self.channel_username, self.channel_id, row["id"]),
                        fetched_at=now,
                    )
                )
        finally:
            con.close()
        return out

    def normalize(self, raw: RawRecord) -> NewsItem:
        d = json.loads(raw.payload)
        text: str = d["text"]
        ents: list[dict] = d.get("entities", [])

        published = datetime.fromisoformat(d["date"])
        title = _extract_title(text)
        article_url = _first_url(ents)
        link = _permalink(d.get("channel_username"), d["channel_id"], d["msg_id"])

        return NewsItem(
            item_id=self.make_item_id(raw.source_id, raw.source_native_id),
            source_id=raw.source_id,
            source_native_id=raw.source_native_id,
            trust_tier=self.trust_tier,
            title=title,
            summary=text[:800],
            body=text,
            url=article_url or link,
            canonical_url=article_url or link,
            source_name=self.source_name,
            published_at=published,
            collected_at=raw.fetched_at,
            language="ko",
            source_meta={"tg_channel": self.channel_username or str(d["channel_id"])},
        )


# ── 채널 목록 ───────────────────────────────────────────────────────────────────

_CHANNELS: list[dict] = [
    {
        "channel_id":      2471352838,
        "source_id":       "tg_insidertracking",
        "source_name":     "미국 주식 인사이더",
        "channel_username": "insidertracking",
    },
]


def default_collectors(db_path: str = _DEFAULT_DB) -> list[TelegramCollector]:
    return [TelegramCollector(db_path=db_path, **ch) for ch in _CHANNELS]
