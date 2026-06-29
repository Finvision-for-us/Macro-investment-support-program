"""§7 라이브 스모크 — 수집부터 후보 생성까지 실데이터 1회전.

실행:  ./.venv/Scripts/python.exe -m ingest2.candidates.run_live

소량 설정(top_k/max_deep)으로 비용을 통제하면서, 시그널/스토리 후보가 실제로
나오고 리서치·출처가 붙는지 눈으로 확인하는 용도. 별도 임시 DB에 수집한다(기존
데이터 오염 방지).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from ..analyze.score import make_gemini_llm as make_impact_llm
from ..analyze.score import score_candidates
from ..classify.basic import run_classify
from ..classify.deep import make_gemini_llm, run_deep_classify
from ..classify.tickers import TickerMap
from ..collect.registry import all_collectors
from ..dedup.cluster import dedup_passed
from ..filter.basic import run_filter
from ..rank.final import rank_final
from ..report import write_report
from ..run import run
from ..store.news_store import NewsStore
from ..store.raw_store import RawStore
from .pipeline import CandidateConfig, generate_candidates

# 스모크 파라미터 (비용 통제)
WINDOW_HOURS = 48
DEEP_CLASSIFY_LIMIT = 12     # 간접티커 보강 (스토리 형성 기회↑), Gemini flash-lite
TOP_K = 30
MAX_DEEP = 2                 # Parallel deep research 최대 건수
DEEP_HIGH_VALUE_SIGNALS = 2  # 스토리가 없어도 고가치 시그널 2건은 deep research

SMOKE_DB = "data/ingest2/smoke_news.db"


def _hr(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def main() -> None:
    # Windows 콘솔(cp949)에서 한글·기호 출력 깨짐/크래시 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    until = datetime.now(UTC)
    since = until - timedelta(hours=WINDOW_HOURS)

    news_store = NewsStore(SMOKE_DB)
    raw_store = RawStore()

    _hr("1. 수집")
    stats = run(all_collectors(), since, until, raw_store=raw_store, news_store=news_store)
    print(f"fetched={stats.fetched} new={stats.stored_new} dup={stats.duplicates}")
    print("by source:", {k: v for k, v in stats.per_source.items()})

    _hr("2. 1차 필터")
    fstats = run_filter(news_store, cutoff_hours=WINDOW_HOURS)  # 스모크: 수집창과 동일
    print(fstats)

    _hr("3. 경량 분류 (결정론)")
    tmap = TickerMap.from_sec()
    cstats = run_classify(news_store, tmap)
    print({k: (dict(v) if hasattr(v, "items") else v) for k, v in cstats.items()})

    _hr("4. 깊은 분류 (Gemini, 간접티커 보강)")
    try:
        dstats = run_deep_classify(news_store, make_gemini_llm(), limit=DEEP_CLASSIFY_LIMIT)
        print({k: (dict(v) if hasattr(v, "items") else v) for k, v in dstats.items()})
    except Exception as ex:  # noqa: BLE001
        print(f"(skipped: {ex})")

    _hr("5. 중복 제거")
    clusters = dedup_passed(news_store)  # 결정론(embedder=None) — 스모크 비용 통제
    print(f"clusters={len(clusters)}")

    _hr("6. §7 후보 생성 + 리서치")
    config = CandidateConfig(
        top_k=TOP_K,
        max_deep=MAX_DEEP,
        deep_high_value_signals=DEEP_HIGH_VALUE_SIGNALS,
    )
    result = generate_candidates(clusters, config, on_log=print)

    _hr("7. §8 AI 영향도 스코어")
    stories = score_candidates(result, llm_fn=make_impact_llm(), on_log=print)
    final_items = rank_final(stories, result)

    _hr("결과 요약")
    print(result.stats)
    print(f"scored={len(stories)} final={len(final_items)}")

    _hr("최종 후보 (§9 랭킹순)")
    for i, item in enumerate(final_items, 1):
        story = item.story
        kind = "STORY" if len(story.event_ids) > 1 else "SIGNAL"
        has_deep = any(eid in result.deep_reports for eid in story.event_ids)
        tickers = ", ".join(story.affected_tickers[:6]) or "(none)"
        print(
            f"\n[{i}] {kind} | final={item.final_score:.3f} | "
            f"impact={story.aggregated_impact:.3f} | "
            f"dir={story.direction} | 이벤트 {len(story.event_ids)} | "
            f"출처 {len(story.all_sources)} | deep={'yes' if has_deep else 'no'}"
        )
        print(f"    랭킹: {', '.join(item.reasons)}")
        print(f"    티커: {tickers}")
        print(f"    제목: {story.title or '(no title)'}")
        if story.narrative_short:
            print(f"    요약: {story.narrative_short[:160]}")

    _hr("리포트 출력")
    paths = write_report(final_items, result, window_hours=WINDOW_HOURS)
    print(f"HTML: {paths.html.resolve()}")
    print(f"JSON: {paths.json.resolve()}")

    news_store.close()


if __name__ == "__main__":
    main()
