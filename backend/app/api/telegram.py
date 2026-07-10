"""텔레그램 채널 메시지 피드 API."""
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

_DB = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "finvision_crawling", "data", "telegram.db",
    )
)

_CHANNELS = {
    2471352838: {"name": "미국 주식 인사이더", "username": "insidertracking"},
}


def _read_feed(hours: int, limit: int) -> list[dict]:
    if not os.path.exists(_DB):
        return []

    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    con = sqlite3.connect(_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT m.id, m.post_id, m.channel_id, m.date, m.text
            FROM   messages m
            WHERE  m.channel_id IN ({})
              AND  m.text IS NOT NULL AND m.text != ''
              AND  m.date >= ?
            ORDER  BY m.date DESC
            LIMIT  ?
            """.format(",".join(str(c) for c in _CHANNELS)),
            (since, limit),
        ).fetchall()

        seen: set[int] = set()
        items = []
        for r in rows:
            pid = r["post_id"]
            if pid in seen:
                continue
            seen.add(pid)

            text = (r["text"] or "").strip()
            if len(text) < 10:
                continue

            ch = _CHANNELS.get(r["channel_id"], {})
            username = ch.get("username")

            # 첫 URL 엔티티 조회
            url_row = con.execute(
                """SELECT value FROM entities
                   WHERE post_id=? AND channel_id=? AND type='url'
                     AND value LIKE 'http%' AND value NOT LIKE '%t.me%'
                   LIMIT 1""",
                (pid, r["channel_id"]),
            ).fetchone()
            url = url_row["value"] if url_row else None

            permalink = (
                f"https://t.me/{username}/{r['id']}"
                if username
                else f"https://t.me/c/{r['channel_id']}/{r['id']}"
            )

            items.append({
                "id":           pid,
                "channel_id":   r["channel_id"],
                "channel_name": ch.get("name", ""),
                "date":         r["date"],
                "text":         text,
                "url":          url,
                "permalink":    permalink,
            })
    finally:
        con.close()

    return items


@router.get("/feed")
def telegram_feed(
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=60, ge=1, le=200),
):
    items = _read_feed(hours, limit)
    return {"count": len(items), "items": items}
