"""§1~§7 공통 코어 — 수집→필터→분류→중복제거→후보생성→AI 스코어.

두 진입점이 이 코어를 공유한다:
  - ingest2/candidates/run_live.py  → 이후 §9 랭킹 + §10 리포트(top10.json/html)
  - run_ingest2_web.py              → 이후 ripple + lifecycle + macro (stories_latest.json)

이전에는 두 파일이 §1~§7을 각자 복붙해 값이 한쪽에만 반영되는 드리프트가 있었다.
이제 파이프라인 로직은 여기 한 곳에만 존재한다.
"""
from __future__ import annotations

import os
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
WINDOW_HOURS = 48
DEEP_CLASSIFY_LIMIT = 12     # 간접티커 보강 (스토리 형성 기회↑), Gemini flash-lite
TOP_K = 30
MAX_DEEP = 2                 # Parallel deep research 최대 건수
DEEP_HIGH_VALUE_SIGNALS = 2  # 스토리가 없어도 고가치 시그널 N건은 deep research

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


def _hr(on_log: Callable[[str], None], title: str) -> None:
    on_log(f"\n{'=' * 8} {title} {'=' * 8}")


def run_core(
    params: PipelineParams | None = None,
    on_log: Callable[[str], None] = print,
) -> PipelineCoreResult:
    """§1~§7을 1회전. 이후 단계는 호출자가 result.stories/result로 이어간다."""
    params = params or PipelineParams()

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
    stats = run(all_collectors(), since, until, raw_store=raw_store, news_store=news_store)
    on_log(f"fetched={stats.fetched} new={stats.stored_new} dup={stats.duplicates}")
    on_log(f"by source: {dict(stats.per_source)}")

    _hr(on_log, "2. 1차 필터")
    fstats = run_filter(news_store, cutoff_hours=params.window_hours)
    on_log(str(fstats))

    _hr(on_log, "3. 경량 분류 (결정론)")
    tmap = TickerMap.from_sec()
    cstats = run_classify(news_store, tmap)
    on_log(str({k: (dict(v) if hasattr(v, "items") else v) for k, v in cstats.items()}))

    _hr(on_log, "4. 깊은 분류 (Gemini, 간접티커 보강)")
    try:
        dstats = run_deep_classify(news_store, make_gemini_llm(), limit=params.deep_classify_limit)
        on_log(str({k: (dict(v) if hasattr(v, "items") else v) for k, v in dstats.items()}))
    except Exception as ex:  # noqa: BLE001
        on_log(f"(skipped: {ex})")

    _hr(on_log, "5. 중복 제거")
    clusters = dedup_passed(news_store)
    on_log(f"clusters={len(clusters)}")

    _hr(on_log, "6. §7 후보 생성 + 리서치")
    config = CandidateConfig(
        top_k=params.top_k,
        max_deep=params.max_deep,
        deep_high_value_signals=params.deep_high_value_signals,
    )
    result = generate_candidates(clusters, config, on_log=on_log)

    _hr(on_log, "7. §8 AI 영향도 스코어")
    stories = score_candidates(result, llm_fn=make_impact_llm(), on_log=on_log)
    on_log(f"scored={len(stories)} stories")

    return PipelineCoreResult(
        news_store=news_store,
        clusters=clusters,
        result=result,
        stories=stories,
    )
