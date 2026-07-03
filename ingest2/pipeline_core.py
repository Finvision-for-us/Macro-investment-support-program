"""§1~§7 공통 코어 — 수집→필터→분류→중복제거→후보생성→AI 스코어.

진입점: run_ingest2_web.py → §8 ripple + §9 lifecycle + §10 macro → data/stories_latest.json
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .analyze.score import make_gemini_llm as make_impact_llm
from .analyze.score import score_candidates
from .candidates.pipeline import CandidateConfig, generate_candidates
from .classify.basic import run_classify
from .classify.deep import make_gemini_llm, run_deep_classify
from .classify.tickers import TickerMap
from .collect.registry import all_collectors
from .dedup.cluster import dedup_passed
from .filter.basic import run_filter
from .run import run
from .store.news_store import NewsStore
from .store.raw_store import RawStore

# ── 파이프라인 파라미터 기본값 (비용 통제) ──
# 두 진입점의 단일 출처. 이전엔 run_live.py에만 있고 web은 그걸 import 했다.
WINDOW_HOURS = 12
DEEP_CLASSIFY_LIMIT = 12
TOP_K = 30
MAX_DEEP = 2
DEEP_HIGH_VALUE_SIGNALS = 2

SMOKE_DB = "data/ingest2/smoke_news.db"


@dataclass
class PipelineParams:
    window_hours: int = WINDOW_HOURS
    deep_classify_limit: int = DEEP_CLASSIFY_LIMIT
    top_k: int = TOP_K
    max_deep: int = MAX_DEEP
    deep_high_value_signals: int = DEEP_HIGH_VALUE_SIGNALS
    db_path: str = SMOKE_DB
    fresh: bool = False  # True면 기존 db 삭제 후 새로 수집


@dataclass
class PipelineCoreResult:
    news_store: NewsStore
    clusters: list
    result: object          # CandidateResult (candidates.pipeline)
    stories: list           # score_candidates 결과
    timings: dict[str, float] = None  # 단계별 소요시간 (초)


def _hr(on_log: Callable[[str], None], title: str) -> None:
    on_log(f"\n{'=' * 8} {title} {'=' * 8}")


def _elapsed(on_log: Callable[[str], None], t0: float) -> None:
    on_log(f"  → {time.perf_counter() - t0:.1f}s")


def run_core(
    params: PipelineParams | None = None,
    on_log: Callable[[str], None] = print,
) -> PipelineCoreResult:
    """§1~§7을 1회전. 이후 단계는 호출자가 result.stories/result로 이어간다."""
    params = params or PipelineParams()
    total_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    if params.fresh and os.path.exists(params.db_path):
        on_log(f"기존 db 삭제 후 새로 수집: {params.db_path}")
        try:
            os.remove(params.db_path)
        except OSError as e:  # noqa: BLE001
            on_log(f"db 삭제 실패(잠금?): {e}")

    until = datetime.now(UTC)
    since = until - timedelta(hours=params.window_hours)

    news_store = NewsStore(params.db_path)
    raw_store = RawStore()

    _hr(on_log, "1. 수집")
    t0 = time.perf_counter()
    stats = run(all_collectors(), since, until, raw_store=raw_store, news_store=news_store)
    on_log(f"fetched={stats.fetched} new={stats.stored_new} dup={stats.duplicates}")
    on_log(f"by source: {dict(stats.per_source)}")
    timings["§1 수집"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    _hr(on_log, "2. 1차 필터")
    t0 = time.perf_counter()
    fstats = run_filter(news_store, cutoff_hours=params.window_hours)
    on_log(str(fstats))
    timings["§2 필터"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    _hr(on_log, "3. 경량 분류 (결정론)")
    t0 = time.perf_counter()
    tmap = TickerMap.from_sec()
    cstats = run_classify(news_store, tmap)
    on_log(str({k: (dict(v) if hasattr(v, "items") else v) for k, v in cstats.items()}))
    timings["§3 경량분류"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    _hr(on_log, "4. 깊은 분류 (Gemini, 간접티커 보강)")
    t0 = time.perf_counter()
    try:
        dstats = run_deep_classify(news_store, make_gemini_llm(), limit=params.deep_classify_limit)
        on_log(str({k: (dict(v) if hasattr(v, "items") else v) for k, v in dstats.items()}))
    except Exception as ex:  # noqa: BLE001
        on_log(f"(skipped: {ex})")
    timings["§4 딥분류"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    _hr(on_log, "5. 중복 제거")
    t0 = time.perf_counter()
    clusters = dedup_passed(news_store)
    on_log(f"clusters={len(clusters)}")
    timings["§5 중복제거"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    _hr(on_log, "6. §7 후보 생성 + 리서치")
    t0 = time.perf_counter()
    config = CandidateConfig(
        top_k=params.top_k,
        max_deep=params.max_deep,
        deep_high_value_signals=params.deep_high_value_signals,
    )
    result = generate_candidates(clusters, config, on_log=on_log)
    timings["§6 후보+리서치"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    _hr(on_log, "7. §8 AI 영향도 스코어")
    t0 = time.perf_counter()
    stories = score_candidates(result, llm_fn=make_impact_llm(), on_log=on_log)
    on_log(f"scored={len(stories)} stories")
    timings["§7 AI스코어"] = time.perf_counter() - t0
    _elapsed(on_log, t0)

    timings["[core 합계]"] = time.perf_counter() - total_t0
    on_log("\n" + "─" * 40)
    on_log("⏱  단계별 소요시간 (§1~§7)")
    for label, sec in timings.items():
        bar = "█" * max(1, int(sec / max(timings.values()) * 20))
        on_log(f"  {label:<16} {sec:6.1f}s  {bar}")
    on_log("─" * 40)

    return PipelineCoreResult(
        news_store=news_store,
        clusters=clusters,
        result=result,
        stories=stories,
        timings=timings,
    )
