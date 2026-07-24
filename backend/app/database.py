import aiosqlite
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "finvision.db")


def _backup_db(reason: str) -> str | None:
    """파괴적 마이그레이션 직전 DB 스냅샷 백업. 실패해도 기동은 계속."""
    if not os.path.exists(DB_PATH):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = f"{DB_PATH}.{ts}.{reason}.bak"
    try:
        shutil.copy2(DB_PATH, dst)
        print(f"[init_db] DB 백업 생성: {os.path.basename(dst)}")
        return dst
    except Exception as e:  # noqa: BLE001
        print(f"[init_db] DB 백업 실패(무시하고 진행): {e}")
        return None


async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        yield db

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                company_name TEXT,
                buy_price REAL NOT NULL,
                quantity REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── 실적 시뮬레이터 캐시 테이블 ──
        # 기존 테이블 마이그레이션: estimate_verified 컬럼 없으면 재생성
        cursor = await db.execute("PRAGMA table_info(earnings_surprises)")
        cols = [row[1] for row in await cursor.fetchall()]
        if cols and ("period_end" not in cols or "estimate_verified" not in cols):
            # 캐시 테이블을 재생성(DROP)하기 전에 스냅샷 백업 — 재계산 비용이 큰
            # earnings 캐시(수천 행)를 실수로 잃지 않도록 보험.
            _backup_db("earnings_migration")
            await db.execute("DROP TABLE IF EXISTS earnings_surprises")
            await db.execute("DROP TABLE IF EXISTS earnings_price_reactions")
            await db.execute("DELETE FROM cache_metadata WHERE data_type IN ('earnings_surprises','price_reactions')")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS earnings_surprises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                period_end TEXT NOT NULL,
                report_date TEXT,
                period TEXT,
                eps_estimate REAL,
                eps_actual REAL,
                surprise_pct REAL,
                estimate_verified INTEGER DEFAULT 0,
                estimate_source_count INTEGER DEFAULT 0,
                UNIQUE(ticker, period_end)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS earnings_price_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                earnings_date TEXT NOT NULL,
                pre_3d_change REAL,
                reaction_1d_change REAL,
                post_3d_change REAL,
                post_5d_change REAL,
                close_on_date REAL,
                UNIQUE(ticker, earnings_date)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cache_metadata (
                ticker TEXT NOT NULL,
                data_type TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                PRIMARY KEY(ticker, data_type)
            )
        """)
        # ── 가이던스 AI 분석 캐시 테이블 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guidance_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                period_end TEXT NOT NULL,
                report_date TEXT,
                filing_url TEXT,
                guidance_summary TEXT,
                key_themes TEXT,
                sentiment_score REAL,
                revenue_guidance TEXT,
                margin_guidance TEXT,
                specific_numbers TEXT,
                ai_annotation TEXT,
                impact_factor TEXT,
                raw_response TEXT,
                analyzed_at TEXT NOT NULL,
                UNIQUE(ticker, period_end)
            )
        """)
        # ── AI 종목 프로필 캐시 (경쟁사 + 핵심지표) ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stock_profile_ai (
                ticker TEXT PRIMARY KEY,
                competitors_json TEXT,
                key_metrics_json TEXT,
                analyzed_at TEXT NOT NULL,
                profile_hash TEXT
            )
        """)
        # ── additive migration (비파괴): 기존 DB에는 CREATE가 skip되므로
        #    profile_hash 컬럼이 없으면 ALTER ADD COLUMN으로만 추가한다.
        #    DROP/DELETE 금지 — 기존 cache row를 보존한다. 옛 row는 NULL로 남아 old cache로 감지된다.
        cursor = await db.execute("PRAGMA table_info(stock_profile_ai)")
        spa_cols = [row[1] for row in await cursor.fetchall()]
        if "profile_hash" not in spa_cols:
            await db.execute("ALTER TABLE stock_profile_ai ADD COLUMN profile_hash TEXT")
        # ── 심층 리서치 채팅 세션/메시지 (deep_research/router.py 세션 API가 사용.
        #    누락돼 있어 세션 API 전체가 500 → 채팅 프롬프트 증발 버그의 원인이었음) ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS research_sessions (
                id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                title TEXT,
                mode TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS research_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_research_messages_session
            ON research_messages(session_id, created_at)
        """)
        await db.commit()
