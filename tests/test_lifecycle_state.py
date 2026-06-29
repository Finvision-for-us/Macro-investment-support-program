"""M4 Day 5~6 ``lifecycle.state`` 단위 테스트."""
from __future__ import annotations

import pytest

from src.lifecycle import state, store


def _ls(
    sid: str,
    *,
    tickers: list[str] | None = None,
    parent: str | None = None,
    first_seen: str = "2026-05-27",
    last_seen: str = "2026-05-27",
    s: str = "active",
) -> store.LifecycleStory:
    return store.LifecycleStory(
        story_id=sid,
        title=sid,
        tickers=tickers or ["NVDA"],
        score=0.5,
        event_ids=[f"e-{sid}"],
        parent_story_id=parent,
        first_seen_date=first_seen,
        last_seen_date=last_seen,
        state=s,
    )


def _snap(date: str, stories: list[store.LifecycleStory]) -> store.Snapshot:
    return store.Snapshot(date=date, generated_at=f"{date}T00:00:00Z", stories=stories)


# ----- 기본 라벨링 ---------------------------------------------------------


def test_new_story_without_parent_is_active():
    today_linked = [_ls("t1", parent=None)]
    out = state.label_today(today_linked, previous=None, today_date="2026-05-27")
    assert len(out) == 1
    assert out[0].state == "active"
    assert out[0].last_seen_date == "2026-05-27"


def test_linked_story_with_parent_is_evolving():
    today_linked = [_ls("t1", parent="y1")]
    prev = _snap("2026-05-26", [_ls("y1", first_seen="2026-05-25", last_seen="2026-05-26")])
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert out[0].state == "evolving"
    assert out[0].last_seen_date == "2026-05-27"


def test_last_seen_always_updated_to_today():
    today_linked = [_ls("t1", parent=None, last_seen="2020-01-01")]
    out = state.label_today(today_linked, previous=None, today_date="2026-05-27")
    assert out[0].last_seen_date == "2026-05-27"


# ----- 이월 / resolved 전환 ------------------------------------------------


def test_carry_over_unmatched_yesterday_story():
    """어제 active 스토리가 오늘 매칭 안 됐고 1일 차이면 active 유지."""
    today_linked: list[store.LifecycleStory] = []
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-26", s="active")])
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert len(out) == 1
    assert out[0].story_id == "y1"
    assert out[0].state == "active"  # 1일 무신호 — 아직 resolved 아님
    assert out[0].last_seen_date == "2026-05-26"  # 갱신 X


def test_three_days_no_signal_becomes_resolved():
    today_linked: list[store.LifecycleStory] = []
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-24", s="active")])
    # today 2026-05-27, last_seen 2026-05-24 → 3일 차이
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert out[0].state == "resolved"


def test_two_days_no_signal_still_active():
    today_linked: list[store.LifecycleStory] = []
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-25", s="active")])
    # today 2026-05-27, last_seen 2026-05-25 → 2일 차이 < 3
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert out[0].state == "active"


def test_matched_parent_not_carried_over():
    """parent 로 매칭된 어제 스토리는 이월 list 에 중복으로 들어가지 않음."""
    today_linked = [_ls("t1", parent="y1")]
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-26", s="active")])
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    ids = [s.story_id for s in out]
    assert ids == ["t1"]  # y1은 이월 안 됨


def test_already_resolved_not_carried_over():
    """어제 이미 resolved 였던 건 스냅샷이 계속 비대해지지 않도록 drop."""
    today_linked: list[store.LifecycleStory] = []
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-20", s="resolved")])
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert out == []


def test_evolving_carried_over_keeps_state_if_within_window():
    today_linked: list[store.LifecycleStory] = []
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-26", s="evolving")])
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert out[0].state == "evolving"  # 1일 무신호 — 유지


def test_custom_resolved_after_days():
    today_linked: list[store.LifecycleStory] = []
    prev = _snap("2026-05-26", [_ls("y1", last_seen="2026-05-26")])
    out = state.label_today(
        today_linked, prev, today_date="2026-05-27", resolved_after_days=1
    )
    assert out[0].state == "resolved"


# ----- 엣지 ----------------------------------------------------------------


def test_no_previous_returns_only_today():
    today_linked = [_ls("t1"), _ls("t2", parent="y_phantom")]
    out = state.label_today(today_linked, previous=None, today_date="2026-05-27")
    assert [s.story_id for s in out] == ["t1", "t2"]
    assert out[0].state == "active"
    assert out[1].state == "evolving"  # parent 만 있으면 evolving (실제 prev 없어도)


def test_empty_today_with_empty_previous_returns_empty():
    out = state.label_today([], previous=None, today_date="2026-05-27")
    assert out == []


def test_today_first_followed_by_carry_overs():
    """결과 순서: 오늘 발견된 스토리들 먼저, 이월된 어제 스토리가 뒤."""
    today_linked = [_ls("t1", parent="y1")]
    prev = _snap(
        "2026-05-26",
        [
            _ls("y1", last_seen="2026-05-26"),  # matched → 이월 안 함
            _ls("y2", last_seen="2026-05-26"),  # unmatched → 이월
        ],
    )
    out = state.label_today(today_linked, prev, today_date="2026-05-27")
    assert [s.story_id for s in out] == ["t1", "y2"]


def test_does_not_mutate_inputs():
    today_original = _ls("t1", parent=None, last_seen="2020-01-01")
    prev_original = _ls("y1", last_seen="2026-05-26", s="active")
    today = [today_original]
    prev = _snap("2026-05-26", [prev_original])
    state.label_today(today, prev, today_date="2026-05-27")
    assert today_original.state == "active"  # 미변경 (이미 active였지만 last_seen 확인)
    assert today_original.last_seen_date == "2020-01-01"
    assert prev_original.last_seen_date == "2026-05-26"
    assert prev_original.state == "active"


def test_bad_today_date_raises():
    with pytest.raises(ValueError):
        state.label_today([], previous=None, today_date="not-a-date")
