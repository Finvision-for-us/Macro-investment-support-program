"""
SQLite 저장소.
테이블:
  channels         -- 수집 대상 채널 정보
  messages         -- 전체 메시지 원문 (post_id로 포스트 묶음)
  media            -- 미디어 파일 메타 + 로컬 경로 (post_id로 포스트 묶음)
  entities         -- URL / mention / hashtag 추출
  collection_state -- 채널별 마지막 수집 message_id
뷰:
  posts            -- 포스트 단위 통합 조회 (텍스트 + 미디어 목록)
"""
import aiosqlite
import config

CREATE_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS channels (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    title       TEXT,
    type        TEXT,
    joined_at   TEXT,
    added_by    TEXT,
    UNIQUE(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER,
    channel_id      INTEGER NOT NULL,
    post_id         INTEGER NOT NULL,   -- grouped_id 있으면 grouped_id, 없으면 message_id
    date            TEXT NOT NULL,
    sender_id       INTEGER,
    sender_name     TEXT,
    sender_username TEXT,
    text            TEXT,
    raw_text        TEXT,
    reply_to_msg_id INTEGER,
    fwd_from_id     INTEGER,
    fwd_from_name   TEXT,
    grouped_id      INTEGER,
    views           INTEGER,
    forwards        INTEGER,
    edit_date       TEXT,
    pinned          INTEGER DEFAULT 0,
    PRIMARY KEY (id, channel_id),
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

CREATE TABLE IF NOT EXISTS media (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    post_id         INTEGER NOT NULL,   -- 해당 포스트의 post_id (messages.post_id와 동일)
    media_type      TEXT NOT NULL,
    mime_type       TEXT,
    file_name       TEXT,
    file_size       INTEGER,
    duration_sec    INTEGER,
    width           INTEGER,
    height          INTEGER,
    local_path      TEXT,
    remote_file_id  TEXT,
    thumbnail_path  TEXT,
    url             TEXT,
    title           TEXT,
    description     TEXT,
    FOREIGN KEY (message_id, channel_id) REFERENCES messages(id, channel_id)
);

CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    post_id     INTEGER NOT NULL,
    type        TEXT NOT NULL,
    value       TEXT NOT NULL,
    offset      INTEGER,
    length      INTEGER,
    FOREIGN KEY (message_id, channel_id) REFERENCES messages(id, channel_id)
);

CREATE TABLE IF NOT EXISTS collection_state (
    channel_id      INTEGER PRIMARY KEY,
    last_message_id INTEGER DEFAULT 0,
    last_collected  TEXT,
    total_collected INTEGER DEFAULT 0,
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

-- ── 포스트 단위 통합 뷰 ──────────────────────────────────────────────────────
-- 한 포스트 = 텍스트(합산) + 미디어 목록
-- 앨범(grouped_id)은 같은 post_id로 묶임
-- 메시지 집계와 미디어 집계를 서브쿼리로 분리해 중복 방지
CREATE VIEW IF NOT EXISTS posts AS
SELECT
    msg_agg.post_id,
    msg_agg.channel_id,
    msg_agg.date,
    msg_agg.sender_id,
    msg_agg.sender_name,
    msg_agg.sender_username,
    msg_agg.text,
    msg_agg.message_count,
    msg_agg.reply_to_msg_id,
    msg_agg.fwd_from_id,
    msg_agg.fwd_from_name,
    msg_agg.views,
    msg_agg.forwards,
    msg_agg.pinned,
    COALESCE(med_agg.media_types, '')   AS media_types,
    COALESCE(med_agg.media_count, 0)    AS media_count,
    COALESCE(med_agg.media_list, '')    AS media_list
FROM (
    SELECT
        post_id,
        channel_id,
        MIN(date)           AS date,
        MIN(sender_id)      AS sender_id,
        MIN(sender_name)    AS sender_name,
        MIN(sender_username) AS sender_username,
        TRIM(GROUP_CONCAT(CASE WHEN text != '' THEN text END, ' ')) AS text,
        COUNT(id)           AS message_count,
        MIN(reply_to_msg_id) AS reply_to_msg_id,
        MIN(fwd_from_id)    AS fwd_from_id,
        MIN(fwd_from_name)  AS fwd_from_name,
        MAX(views)          AS views,
        MAX(forwards)       AS forwards,
        MAX(pinned)         AS pinned
    FROM messages
    GROUP BY post_id, channel_id
) msg_agg
LEFT JOIN (
    SELECT
        post_id,
        channel_id,
        GROUP_CONCAT(DISTINCT media_type)                              AS media_types,
        COUNT(id)                                                      AS media_count,
        GROUP_CONCAT(media_type || ':' || COALESCE(local_path,''), '|') AS media_list
    FROM media
    GROUP BY post_id, channel_id
) med_agg ON med_agg.post_id = msg_agg.post_id AND med_agg.channel_id = msg_agg.channel_id;

CREATE TABLE IF NOT EXISTS analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    analyzed_at     TEXT NOT NULL,
    model           TEXT NOT NULL,          -- 사용한 Gemini 모델명
    prompt          TEXT,                   -- 사용한 프롬프트
    result          TEXT,                   -- Gemini 분석 결과 전문
    image_count     INTEGER DEFAULT 0,      -- 분석에 사용된 이미지 수
    has_text        INTEGER DEFAULT 0,      -- 텍스트 포함 여부
    UNIQUE(post_id, channel_id)             -- 포스트당 1회 분석 (재분석 시 REPLACE)
);

CREATE INDEX IF NOT EXISTS idx_analysis_post      ON analysis(post_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_analysis_date      ON analysis(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_messages_channel   ON messages(channel_id, date);
CREATE INDEX IF NOT EXISTS idx_messages_post      ON messages(post_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_messages_date      ON messages(date);
CREATE INDEX IF NOT EXISTS idx_media_post         ON media(post_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_media_message      ON media(message_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_entities_post      ON entities(post_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_entities_message   ON entities(message_id, channel_id);
"""


async def init_db():
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()


async def upsert_channel(ch: dict):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("""
            INSERT INTO channels (id, username, title, type, joined_at, added_by)
            VALUES (:id, :username, :title, :type, :joined_at, :added_by)
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username,
                title=excluded.title,
                type=excluded.type
        """, ch)
        await db.commit()


async def insert_message(msg: dict):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO messages
            (id, channel_id, post_id, date, sender_id, sender_name, sender_username,
             text, raw_text, reply_to_msg_id, fwd_from_id, fwd_from_name,
             grouped_id, views, forwards, edit_date, pinned)
            VALUES
            (:id, :channel_id, :post_id, :date, :sender_id, :sender_name, :sender_username,
             :text, :raw_text, :reply_to_msg_id, :fwd_from_id, :fwd_from_name,
             :grouped_id, :views, :forwards, :edit_date, :pinned)
        """, msg)
        await db.commit()


async def insert_media(m: dict):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("""
            INSERT INTO media
            (message_id, channel_id, post_id, media_type, mime_type, file_name, file_size,
             duration_sec, width, height, local_path, remote_file_id,
             thumbnail_path, url, title, description)
            VALUES
            (:message_id, :channel_id, :post_id, :media_type, :mime_type, :file_name, :file_size,
             :duration_sec, :width, :height, :local_path, :remote_file_id,
             :thumbnail_path, :url, :title, :description)
        """, m)
        await db.commit()


async def insert_entity(e: dict):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("""
            INSERT INTO entities (message_id, channel_id, post_id, type, value, offset, length)
            VALUES (:message_id, :channel_id, :post_id, :type, :value, :offset, :length)
        """, e)
        await db.commit()


async def upsert_analysis(a: dict):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("""
            INSERT INTO analysis
            (post_id, channel_id, analyzed_at, model, prompt, result, image_count, has_text)
            VALUES (:post_id, :channel_id, :analyzed_at, :model, :prompt, :result, :image_count, :has_text)
            ON CONFLICT(post_id, channel_id) DO UPDATE SET
                analyzed_at = excluded.analyzed_at,
                model       = excluded.model,
                prompt      = excluded.prompt,
                result      = excluded.result,
                image_count = excluded.image_count,
                has_text    = excluded.has_text
        """, a)
        await db.commit()


async def get_unanalyzed_posts(channel_id: int | None = None) -> list[dict]:
    """아직 분석되지 않은 포스트 목록 반환."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if channel_id:
            cur = await db.execute("""
                SELECT p.post_id, p.channel_id, p.date, p.text, p.media_list
                FROM posts p
                LEFT JOIN analysis a ON a.post_id = p.post_id AND a.channel_id = p.channel_id
                WHERE a.id IS NULL AND p.channel_id = ?
                ORDER BY p.date ASC
            """, (channel_id,))
        else:
            cur = await db.execute("""
                SELECT p.post_id, p.channel_id, p.date, p.text, p.media_list
                FROM posts p
                LEFT JOIN analysis a ON a.post_id = p.post_id AND a.channel_id = p.channel_id
                WHERE a.id IS NULL
                ORDER BY p.date ASC
            """)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_last_message_id(channel_id: int) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "SELECT last_message_id FROM collection_state WHERE channel_id=?",
            (channel_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def update_collection_state(channel_id: int, last_msg_id: int, count: int):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("""
            INSERT INTO collection_state (channel_id, last_message_id, last_collected, total_collected)
            VALUES (?, ?, datetime('now'), ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                last_message_id = MAX(last_message_id, excluded.last_message_id),
                last_collected  = excluded.last_collected,
                total_collected = total_collected + excluded.total_collected
        """, (channel_id, last_msg_id, count))
        await db.commit()
