import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.absolute()
sys.path.insert(0, str(ROOT))

from ingest2.candidates.run_live import WINDOW_HOURS, DEEP_CLASSIFY_LIMIT, TOP_K, MAX_DEEP, DEEP_HIGH_VALUE_SIGNALS, SMOKE_DB
from ingest2.analyze.score import make_gemini_llm as make_impact_llm, score_candidates
from ingest2.classify.basic import run_classify
from ingest2.classify.deep import make_gemini_llm, run_deep_classify
from ingest2.classify.tickers import TickerMap
from ingest2.collect.registry import all_collectors
from ingest2.dedup.cluster import dedup_passed
from ingest2.filter.basic import run_filter
from ingest2.run import run
from ingest2.store.news_store import NewsStore
from ingest2.store.raw_store import RawStore
from ingest2.candidates.pipeline import CandidateConfig, generate_candidates

# src imports
from src.causal.ripple import generate_ripples
from src.lifecycle.store import from_story, save_snapshot, load_previous_snapshot
from src.lifecycle import link as life_link, state as life_state
from src.macro import fred as macro_fred, themes as macro_themes
from src.cli import _write_stories_latest

def _hr(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")

def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Optional: clean smoke db for fresh run
    db_path = ROOT / SMOKE_DB
    if db_path.exists():
        print(f"Removing old smoke db at {db_path} for a fresh run...")
        try:
            db_path.unlink()
        except Exception as e:
            print(f"Could not remove old smoke db (probably locked): {e}")

    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=WINDOW_HOURS)

    news_store = NewsStore(str(db_path))
    raw_store = RawStore()

    _hr("1. 수집 (ingest2)")
    stats = run(all_collectors(), since, until, raw_store=raw_store, news_store=news_store)
    print(f"fetched={stats.fetched} new={stats.stored_new} dup={stats.duplicates}")
    print("by source:", {k: v for k, v in stats.per_source.items()})

    _hr("2. 1차 필터")
    fstats = run_filter(news_store, cutoff_hours=WINDOW_HOURS)
    print(fstats)

    _hr("3. 경량 분류 (결정론)")
    tmap = TickerMap.from_sec()
    cstats = run_classify(news_store, tmap)
    print({k: (dict(v) if hasattr(v, "items") else v) for k, v in cstats.items()})

    _hr("4. 깊은 분류 (Gemini, 간접티커 보강)")
    try:
        dstats = run_deep_classify(news_store, make_gemini_llm(), limit=DEEP_CLASSIFY_LIMIT)
        print({k: (dict(v) if hasattr(v, "items") else v) for k, v in dstats.items()})
    except Exception as ex:
        print(f"(skipped: {ex})")

    _hr("5. 중복 제거")
    clusters = dedup_passed(news_store)
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
    print(f"Scored {len(stories)} stories.")

    _hr("8. M3.5 파급효과 (Ripple Effects) 생성")
    enriched_stories = []
    for i, story in enumerate(stories, 1):
        if not story.title:
            enriched_stories.append(story)
            continue
        print(f"[{i}/{len(stories)}] Generating ripple effects for: {story.title[:60]}")
        try:
            ripples = generate_ripples(story)
            story = story.model_copy(update={"ripple_effects": ripples})
            print(f"   -> Added {len(ripples)} ripple effects.")
        except Exception as e:
            print(f"   -> Failed to generate ripples: {e}")
        enriched_stories.append(story)

    _hr("9. M4 Lifecycle 매칭 및 상태 결정")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lifecycle_stories = [from_story(s, on_date=date_str) for s in enriched_stories]

    prev = load_previous_snapshot(date_str)
    if prev is not None:
        print(f"Loaded yesterday snapshot {prev.date} ({len(prev.stories)} stories)")
        today_linked = life_link.link_to_previous(lifecycle_stories, prev)
        final_stories = life_state.label_today(today_linked, prev, today_date=date_str)
    else:
        print("No yesterday snapshot found. All stories initialized as active.")
        final_stories = lifecycle_stories

    _hr("10. 거시지표 (FRED) 및 테마 생성")
    macro_events = []
    try:
        macro_events = macro_fred.fetch_macro_events(emit_days=14, sigma_threshold=1.0)
        print(f"Fetched {len(macro_events)} macro events.")
    except Exception as e:
        print(f"Failed to fetch macro events: {e}")

    themes = []
    try:
        narr_stories = [s for s in enriched_stories if s.title]
        themes = macro_themes.build_themes(narr_stories)
        print(f"Generated {len(themes)} themes.")
    except Exception as e:
        print(f"Failed to generate themes: {e}")

    _hr("11. UI 소스 파일 저장 (data/stories_latest.json)")
    snap_path = save_snapshot(
        final_stories,
        date_str=date_str,
        source_narratives="ingest2_run",
        macro_events=macro_events,
        themes=themes,
    )
    latest_path = _write_stories_latest(
        final_stories, date_str, macro_events=macro_events, themes=themes
    )
    print(f"Saved snapshot -> {snap_path}")
    print(f"Updated UI file -> {latest_path}")

    news_store.close()
    print("\n[SUCCESS] ingest2 pipeline run successfully and UI data updated!")
    print("Now you can open a new terminal, run Next.js app, and view it on http://localhost:3000/today")

if __name__ == "__main__":
    main()
