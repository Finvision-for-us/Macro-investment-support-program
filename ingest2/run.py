"""오케스트레이터 골격 — 등록된 어댑터를 돌려 수집→원본저장→정규화→정규저장.

소스가 무엇이든 동일하게 돈다. 새 소스 추가 시 이 파일은 수정하지 않는다.
실제 어댑터는 P2(RSS)부터 collect/ 아래에 추가된다.
"""
from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

from .collect.base import BaseCollector
from .store.news_store import NewsStore
from .store.raw_store import RawStore


@dataclass
class RunStats:
    fetched: int = 0
    stored_new: int = 0
    duplicates: int = 0
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)


def run(
    collectors: Iterable[BaseCollector],
    since: datetime,
    until: datetime,
    raw_store: RawStore | None = None,
    news_store: NewsStore | None = None,
) -> RunStats:
    raw_store = raw_store or RawStore()
    news_store = news_store or NewsStore()
    stats = RunStats()

    cols = list(collectors)

    # ① 병렬 fetch — 네트워크 I/O만, DB 접근 없음
    def _fetch(col: BaseCollector) -> tuple[BaseCollector, list]:
        try:
            return col, col.fetch(since, until)
        except Exception:  # noqa: BLE001 — 한 소스 실패가 배치를 막지 않게
            return col, []

    with ThreadPoolExecutor(max_workers=len(cols)) as pool:
        fetched_pairs = list(pool.map(_fetch, cols))

    # ② 순차 저장 — SQLite 단일 연결 보호
    for col, raws in fetched_pairs:
        s = stats.per_source.setdefault(col.source_id, {"fetched": 0, "new": 0, "dup": 0})
        for raw in raws:
            raw_store.save(raw)                  # 원본 보관 (보험)
            item = col.normalize(raw)            # 공통 스키마 변환
            inserted = news_store.save(item)     # 정규화 저장 (중복 자동 차단)
            stats.fetched += 1
            s["fetched"] += 1
            if inserted:
                stats.stored_new += 1
                s["new"] += 1
            else:
                stats.duplicates += 1
                s["dup"] += 1
    return stats
