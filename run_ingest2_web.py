import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.absolute()
sys.path.insert(0, str(ROOT))

from ingest2.pipeline_core import SMOKE_DB, PipelineParams, run_core

# src imports — 이 스크립트만의 후처리(§8~§11)
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

    pipeline_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    # §1~§7: 코어에 위임 (web은 매 실행 새 수집 → fresh=True)
    db_path = str(ROOT / SMOKE_DB)
    core = run_core(PipelineParams(db_path=db_path, fresh=True))
    stories = core.stories
    news_store = core.news_store
    if core.timings:
        timings.update(core.timings)
    print(f"Scored {len(stories)} stories.")

    _hr("8. M3.5 파급효과 (Ripple Effects) 생성")
    t0 = time.perf_counter()
    total_stories = len(stories)

    def _enrich_ripple(args: tuple) -> object:
        i, story = args
        if not story.title:
            return story
        print(f"[{i}/{total_stories}] Generating ripple effects for: {story.title[:60]}")
        try:
            ripples = generate_ripples(story)
            print(f"   -> [{i}/{total_stories}] Added {len(ripples)} ripple effects.")
            return story.model_copy(update={"ripple_effects": ripples})
        except Exception as e:
            print(f"   -> [{i}/{total_stories}] Failed: {e}")
            return story

    with ThreadPoolExecutor(max_workers=8) as pool:
        enriched_stories = list(pool.map(_enrich_ripple, enumerate(stories, 1)))
    timings["§8 리플생성"] = time.perf_counter() - t0
    print(f"  → {timings['§8 리플생성']:.1f}s")

    _hr("9. M4 Lifecycle 매칭 및 상태 결정")
    t0 = time.perf_counter()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events_by_id = core.result.events_by_id
    lifecycle_stories = [from_story(s, on_date=date_str, events_by_id=events_by_id) for s in enriched_stories]

    prev = load_previous_snapshot(date_str)
    if prev is not None:
        print(f"Loaded yesterday snapshot {prev.date} ({len(prev.stories)} stories)")
        today_linked = life_link.link_to_previous(lifecycle_stories, prev)
        final_stories = life_state.label_today(today_linked, prev, today_date=date_str)
    else:
        print("No yesterday snapshot found. All stories initialized as active.")
        final_stories = lifecycle_stories
    timings["§9 라이프사이클"] = time.perf_counter() - t0
    print(f"  → {timings['§9 라이프사이클']:.1f}s")

    _hr("10. 거시지표 (FRED) 및 테마 생성")
    t0 = time.perf_counter()
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
    timings["§10 매크로+테마"] = time.perf_counter() - t0
    print(f"  → {timings['§10 매크로+테마']:.1f}s")

    _hr("11. UI 소스 파일 저장 (data/stories_latest.json)")
    t0 = time.perf_counter()
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
    timings["§11 저장"] = time.perf_counter() - t0
    print(f"  → {timings['§11 저장']:.1f}s")

    news_store.close()

    # ── 전체 타이밍 요약 ──
    total = time.perf_counter() - pipeline_t0
    timings["[전체 합계]"] = total
    max_sec = max(timings.values())
    print("\n" + "═" * 46)
    print("⏱  전체 파이프라인 소요시간 요약")
    print("─" * 46)
    for label, sec in timings.items():
        bar = "█" * max(1, int(sec / max_sec * 22))
        marker = " ◀ 최장" if sec == max(v for k, v in timings.items() if k != "[전체 합계]") else ""
        print(f"  {label:<18} {sec:6.1f}s  {bar}{marker}")
    print("═" * 46)

    print("\n[SUCCESS] ingest2 pipeline run successfully and UI data updated!")
    print("Now you can open a new terminal, run Next.js app, and view it on http://localhost:3000/today")

if __name__ == "__main__":
    main()
