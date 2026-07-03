"""§7 라이브 스모크 — 수집부터 후보 생성까지 실데이터 1회전.

실행:  ./.venv/Scripts/python.exe -m ingest2.candidates.run_live

소량 설정(top_k/max_deep)으로 비용을 통제하면서, 시그널/스토리 후보가 실제로
나오고 리서치·출처가 붙는지 눈으로 확인하는 용도. 별도 임시 DB에 수집한다(기존
데이터 오염 방지).

§1~§7 파이프라인 로직은 ingest2/pipeline_core.py에 있다(run_ingest2_web.py와 공유).
이 파일은 코어를 호출한 뒤 §9 랭킹 + §10 리포트만 담당한다.
"""
from __future__ import annotations

import sys

# 하위호환: 기존에 이 모듈에서 상수를 import 하던 코드(run_ingest2_web 등)를 위해 재노출.
from ..pipeline_core import (  # noqa: F401
    DEEP_CLASSIFY_LIMIT,
    DEEP_HIGH_VALUE_SIGNALS,
    MAX_DEEP,
    SMOKE_DB,
    TOP_K,
    WINDOW_HOURS,
    PipelineParams,
    run_core,
)
from ..rank.final import rank_final
from ..report import write_report


def _hr(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def main() -> None:
    # Windows 콘솔(cp949)에서 한글·기호 출력 깨짐/크래시 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    # 스모크: 기존 smoke db에 누적(fresh=False) — 코어가 §1~§7 수행
    core = run_core(PipelineParams(fresh=False))
    stories = core.stories
    result = core.result

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

    core.news_store.close()


if __name__ == "__main__":
    main()
