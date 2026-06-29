"""M4 Day 1~2 ``lifecycle.store`` 단위 테스트."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.causal.schema import Story
from src.lifecycle import store


@pytest.fixture
def tmp_lifecycle(tmp_path, monkeypatch):
    """LIFECYCLE_DIR을 tmp 디렉터리로 교체 (cost_guard 패턴 동일)."""
    d = tmp_path / "lifecycle"
    monkeypatch.setattr(store, "LIFECYCLE_DIR", d)
    return d


def _make_story(sid: str = "s1") -> Story:
    return Story(
        id=sid,
        event_ids=["e1", "e2"],
        title="엔비디아 GTC 키노트",
        narrative_short="AI 인프라 수주 확대 전망.",
        narrative_long="",
        direction="positive",
        confidence=0.7,
        affected_tickers=["NVDA", "AVGO"],
        aggregated_impact=0.85,
    )


def test_from_story_initializes_active(tmp_lifecycle):
    ls = store.from_story(_make_story("s1"), on_date="2026-05-27")
    assert ls.story_id == "s1"
    assert ls.state == "active"
    assert ls.first_seen_date == "2026-05-27"
    assert ls.last_seen_date == "2026-05-27"
    assert ls.parent_story_id is None
    assert ls.similarity is None
    assert ls.tickers == ["NVDA", "AVGO"]
    assert ls.score == pytest.approx(0.85)


def test_save_then_load_round_trip(tmp_lifecycle):
    ls = store.from_story(_make_story("s1"), on_date="2026-05-27")
    path = store.save_snapshot([ls], date_str="2026-05-27", source_narratives="x.json")
    assert path.exists()
    snap = store.load_snapshot("2026-05-27")
    assert snap is not None
    assert snap.date == "2026-05-27"
    assert snap.source_narratives == "x.json"
    assert len(snap.stories) == 1
    assert snap.stories[0].story_id == "s1"
    assert snap.stories[0].title == "엔비디아 GTC 키노트"


def test_save_overwrites_same_date(tmp_lifecycle):
    """같은 날짜 두 번 저장하면 두 번째가 이긴다 (재실행 안전)."""
    ls1 = store.from_story(_make_story("s1"), on_date="2026-05-27")
    ls2 = store.from_story(_make_story("s2"), on_date="2026-05-27")
    store.save_snapshot([ls1], date_str="2026-05-27")
    store.save_snapshot([ls2], date_str="2026-05-27")
    snap = store.load_snapshot("2026-05-27")
    assert snap is not None
    ids = [s.story_id for s in snap.stories]
    assert ids == ["s2"]


def test_load_missing_returns_none(tmp_lifecycle):
    assert store.load_snapshot("2026-01-01") is None


def test_save_rejects_bad_date(tmp_lifecycle):
    with pytest.raises(ValueError):
        store.save_snapshot([], date_str="not-a-date")


def test_list_snapshot_dates_sorted(tmp_lifecycle):
    for d in ["2026-05-25", "2026-05-27", "2026-05-26"]:
        store.save_snapshot([], date_str=d)
    assert store.list_snapshot_dates() == ["2026-05-25", "2026-05-26", "2026-05-27"]


def test_list_snapshot_dates_ignores_non_date_files(tmp_lifecycle):
    store.save_snapshot([], date_str="2026-05-27")
    (tmp_lifecycle / "README.json").write_text("{}", encoding="utf-8")
    assert store.list_snapshot_dates() == ["2026-05-27"]


def test_list_snapshot_dates_days_filter(tmp_lifecycle):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 오늘
    store.save_snapshot([], date_str=today)
    # 100일 전 → days=7 필터에 빠져야 함
    store.save_snapshot([], date_str="2020-01-01")
    recent = store.list_snapshot_dates(days=7)
    assert today in recent
    assert "2020-01-01" not in recent


def test_load_previous_snapshot(tmp_lifecycle):
    store.save_snapshot([store.from_story(_make_story("a"), "2026-05-25")], date_str="2026-05-25")
    store.save_snapshot([store.from_story(_make_story("b"), "2026-05-26")], date_str="2026-05-26")
    prev = store.load_previous_snapshot("2026-05-27")
    assert prev is not None
    assert prev.date == "2026-05-26"
    assert prev.stories[0].story_id == "b"


def test_load_previous_snapshot_none_when_first_day(tmp_lifecycle):
    store.save_snapshot([], date_str="2026-05-27")
    assert store.load_previous_snapshot("2026-05-27") is None


def test_snapshot_json_is_human_readable(tmp_lifecycle):
    """JSON 출력은 indent 2 — 깃 diff 가 읽힘."""
    ls = store.from_story(_make_story("s1"), on_date="2026-05-27")
    path = store.save_snapshot([ls], date_str="2026-05-27")
    raw = path.read_text(encoding="utf-8")
    assert "\n  " in raw  # 2-space indent
    parsed = json.loads(raw)
    assert parsed["stories"][0]["story_id"] == "s1"


def test_lifecycle_story_rejects_bad_state(tmp_lifecycle):
    with pytest.raises(ValidationError):
        store.LifecycleStory(
            story_id="x",
            title="t",
            first_seen_date="2026-05-27",
            last_seen_date="2026-05-27",
            state="zombie",  # not in Literal
        )
