"""M4 Day 5~6: lifecycle 상태 라벨링 (PROJECT_SPEC §12.2).

상태 3종:

- **active**   : 오늘 새로 생긴 스토리 (어제 parent 없음)
- **evolving** : 어제 본 스토리에 오늘 새 이벤트가 합류 (parent 있음)
- **resolved** : 마지막 신호 후 ``resolved_after_days`` 일 이상 새 신호 없음

오늘 스냅샷에는 두 종류가 들어간다:

1. ``today_linked`` — link.py 가 채운, 오늘 발견된 스토리 (active 또는 evolving)
2. **이월** — 어제 스냅샷 중 오늘 매칭되지 않은 스토리 (state 유지하거나 resolved 전환).
   이미 resolved 였다면 더는 이월하지 않아 스냅샷 무한 누적을 막는다.
"""
from __future__ import annotations

from datetime import date, datetime

from src.lifecycle.store import DATE_FMT, LifecycleStory, Snapshot

RESOLVED_AFTER_DAYS = 3


def _parse(d: str) -> date:
    return datetime.strptime(d, DATE_FMT).date()


def _days_since(last_seen: str, today: str) -> int:
    return (_parse(today) - _parse(last_seen)).days


def label_today(
    today_linked: list[LifecycleStory],
    previous: Snapshot | None,
    today_date: str,
    *,
    resolved_after_days: int = RESOLVED_AFTER_DAYS,
) -> list[LifecycleStory]:
    """오늘 스냅샷에 들어갈 최종 LifecycleStory 목록을 만든다.

    Args:
        today_linked: ``link.link_to_previous`` 결과. 각 항목의 ``parent_story_id``
            존재 여부로 active/evolving 가른다. 모두 ``last_seen_date`` 가 오늘로 갱신됨.
        previous: 어제 스냅샷 (있다면). 오늘 매칭 안 된 어제 스토리는 이월하며
            적절히 resolved 로 전환.
        today_date: ``YYYY-MM-DD``. 이 함수가 'today' 로 간주하는 날짜.
        resolved_after_days: 마지막 신호 후 N 일 무신호면 resolved (기본 3).

    Returns:
        오늘 새로 발견된 스토리 + 이월된 어제 스토리. 원본은 변경하지 않음.
    """
    _parse(today_date)  # 형식 검증

    out: list[LifecycleStory] = []
    seen_ids: set[str] = set()

    # 1) 오늘 발견된 스토리 — link 결과를 상태 라벨링
    for s in today_linked:
        copy = s.model_copy()
        copy.state = "evolving" if copy.parent_story_id else "active"
        copy.last_seen_date = today_date
        out.append(copy)
        seen_ids.add(copy.story_id)

    if previous is None or not previous.stories:
        return out

    # 2) 어제 스토리 중 오늘 parent 로 매칭된 ID 집합 — 매칭된 건 이미 today 에 들어 있음
    matched_parent_ids = {
        s.parent_story_id for s in today_linked if s.parent_story_id is not None
    }

    # 3) 매칭 안 된 어제 스토리 이월 처리
    for y in previous.stories:
        if y.story_id in matched_parent_ids:
            continue  # 자식이 이미 today 에 포함됨
        if y.story_id in seen_ids:
            continue  # ID 충돌 보호 (현실에선 거의 없지만)
        if y.state == "resolved":
            continue  # 이미 종결된 건 더 이상 이월하지 않음 (스냅샷 비대 방지)

        carried = y.model_copy()
        if _days_since(carried.last_seen_date, today_date) >= resolved_after_days:
            carried.state = "resolved"
        # else: 이전 state (active 또는 evolving) 유지
        out.append(carried)
        seen_ids.add(carried.story_id)

    return out
