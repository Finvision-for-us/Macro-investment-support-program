"""M4 Day 3~4 ``lifecycle.link`` 단위 테스트.

Gemini 의존성 없이 검증하기 위해 ``embed_fn`` 을 주입한다. 각 텍스트를
미리 정해진 2D 벡터로 매핑해 코사인 유사도를 결정론적으로 만든다.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.lifecycle import link, store


# ----- 헬퍼 ----------------------------------------------------------------


def _ls(
    sid: str,
    *,
    tickers: list[str],
    title: str,
    narrative: str = "",
    first_seen: str = "2026-05-27",
    last_seen: str = "2026-05-27",
) -> store.LifecycleStory:
    return store.LifecycleStory(
        story_id=sid,
        title=title,
        narrative_short=narrative,
        tickers=tickers,
        score=0.5,
        event_ids=[f"e-{sid}"],
        first_seen_date=first_seen,
        last_seen_date=last_seen,
    )


def _snapshot(date: str, stories: list[store.LifecycleStory]) -> store.Snapshot:
    return store.Snapshot(date=date, generated_at=f"{date}T00:00:00Z", stories=stories)


def _angle_vec(theta_rad: float) -> list[float]:
    """단위 원 위 점 → 두 벡터의 cos sim 이 cos(차이각) 이 됨."""
    return [math.cos(theta_rad), math.sin(theta_rad)]


def _make_embed_fn(table: dict[str, list[float]]):
    """텍스트 → 벡터 룩업 함수. 모르는 텍스트는 0 벡터 (cos sim 미정)."""

    def fn(texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            if t not in table:
                raise KeyError(f"테스트 임베딩 테이블에 없는 텍스트: {t!r}")
            rows.append(table[t])
        return np.array(rows, dtype=np.float32)

    return fn


# ----- 테스트 --------------------------------------------------------------


def test_no_previous_returns_copies_unchanged():
    today = [_ls("t1", tickers=["NVDA"], title="A")]
    out = link.link_to_previous(today, previous=None, embed_fn=_make_embed_fn({}))
    assert len(out) == 1
    assert out[0].parent_story_id is None
    assert out[0].similarity is None
    # 원본 동일성: 다른 객체이지만 동일 값
    assert out[0] is not today[0]
    assert out[0].story_id == "t1"


def test_empty_today_returns_empty():
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="X")])
    assert link.link_to_previous([], prev, embed_fn=_make_embed_fn({})) == []


def test_empty_previous_stories_returns_unchanged():
    today = [_ls("t1", tickers=["NVDA"], title="A")]
    prev = _snapshot("2026-05-26", [])
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn({}))
    assert out[0].parent_story_id is None


def test_no_ticker_overlap_no_link():
    today = [_ls("t1", tickers=["NVDA"], title="A")]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["AAPL"], title="B")])
    # 후보 없으면 임베딩 호출되지 않아야 함 — 빈 테이블이어도 통과
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn({}))
    assert out[0].parent_story_id is None
    assert out[0].similarity is None


def test_high_similarity_links_with_parent():
    today = [_ls("t1", tickers=["NVDA"], title="GTC 키노트 후속")]
    prev_story = _ls("y1", tickers=["NVDA"], title="GTC 키노트", first_seen="2026-05-25")
    prev = _snapshot("2026-05-26", [prev_story])
    # 같은 각도 → cos sim = 1.0
    table = {"GTC 키노트 후속": _angle_vec(0.0), "GTC 키노트": _angle_vec(0.0)}
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "y1"
    assert out[0].similarity == pytest.approx(1.0, abs=1e-4)
    assert out[0].linked_at is not None
    # first_seen 상속
    assert out[0].first_seen_date == "2026-05-25"


def test_low_similarity_below_threshold_no_link():
    today = [_ls("t1", tickers=["NVDA"], title="A")]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="B")])
    # 60도 차이 → cos = 0.5, threshold 0.75 미만
    table = {"A": _angle_vec(0.0), "B": _angle_vec(math.pi / 3)}
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id is None
    # first_seen 그대로
    assert out[0].first_seen_date == "2026-05-27"


def test_picks_best_among_multiple_candidates():
    today = [_ls("t1", tickers=["NVDA"], title="T")]
    prev_a = _ls("yA", tickers=["NVDA"], title="A", first_seen="2026-05-24")
    prev_b = _ls("yB", tickers=["NVDA"], title="B", first_seen="2026-05-25")
    prev_c = _ls("yC", tickers=["NVDA"], title="C", first_seen="2026-05-26")
    prev = _snapshot("2026-05-26", [prev_a, prev_b, prev_c])
    # T(0°), A(45°), B(10°), C(80°) → B가 가장 가까움 (cos≈0.985)
    table = {
        "T": _angle_vec(0.0),
        "A": _angle_vec(math.radians(45)),
        "B": _angle_vec(math.radians(10)),
        "C": _angle_vec(math.radians(80)),
    }
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "yB"
    assert out[0].first_seen_date == "2026-05-25"
    assert out[0].similarity is not None
    assert out[0].similarity > 0.98


def test_ticker_overlap_filters_out_non_candidates():
    """B가 의미적으론 더 비슷해도 ticker 안 겹치면 후보에서 빠짐 — A 가 채택."""
    today = [_ls("t1", tickers=["NVDA"], title="T")]
    prev_a = _ls("yA", tickers=["NVDA"], title="A")  # 후보
    prev_b = _ls("yB", tickers=["AAPL"], title="B")  # 후보 X
    prev = _snapshot("2026-05-26", [prev_a, prev_b])
    table = {
        "T": _angle_vec(0.0),
        "A": _angle_vec(math.radians(20)),  # cos ≈ 0.94
        # B는 후보 아니므로 임베딩 호출 안 됨 → 테이블 미포함 OK
    }
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "yA"


def test_min_ticker_overlap_parameter():
    """min_ticker_overlap=2 로 올리면 1개만 겹치는 후보는 탈락."""
    today = [_ls("t1", tickers=["NVDA", "AMD"], title="T")]
    one_overlap = _ls("yA", tickers=["NVDA"], title="A")
    two_overlap = _ls("yB", tickers=["NVDA", "AMD"], title="B")
    prev = _snapshot("2026-05-26", [one_overlap, two_overlap])
    table = {"T": _angle_vec(0.0), "B": _angle_vec(0.0)}
    out = link.link_to_previous(
        today, prev, min_ticker_overlap=2, embed_fn=_make_embed_fn(table)
    )
    assert out[0].parent_story_id == "yB"


def test_custom_threshold_applies():
    """threshold 0.99로 올리면 cos=0.95 매칭도 떨어짐."""
    today = [_ls("t1", tickers=["NVDA"], title="T")]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="A")])
    # ~18도 차이, cos ≈ 0.95
    table = {"T": _angle_vec(0.0), "A": _angle_vec(math.radians(18))}
    out = link.link_to_previous(
        today, prev, sim_threshold=0.99, embed_fn=_make_embed_fn(table)
    )
    assert out[0].parent_story_id is None


def test_does_not_mutate_input_stories():
    today_original = _ls("t1", tickers=["NVDA"], title="A")
    today = [today_original]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="A", first_seen="2026-05-25")])
    table = {"A": _angle_vec(0.0)}
    link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    # 원본은 그대로
    assert today_original.parent_story_id is None
    assert today_original.first_seen_date == "2026-05-27"


def test_narrative_short_included_in_embedding_text():
    """narrative_short 있으면 임베딩 텍스트가 'title\\n\\nnarrative_short'."""
    today = [_ls("t1", tickers=["NVDA"], title="T", narrative="추가 본문")]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="Y", narrative="다른 본문")])
    expected_today = "T\n\n추가 본문"
    expected_yest = "Y\n\n다른 본문"
    table = {expected_today: _angle_vec(0.0), expected_yest: _angle_vec(0.0)}
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "y1"


def test_empty_text_story_skipped_from_embedding():
    """title/narrative 모두 비어있는 today 스토리는 임베딩 호출에서 빠지고 unlinked로 반환.

    Gemini 가 빈 content 를 거부하므로 (실제로 narratives top N 외 스토리는 비어있음).
    """
    today = [
        _ls("t1", tickers=["NVDA"], title="좋은 제목"),
        _ls("t2", tickers=["NVDA"], title="", narrative=""),  # 빈 텍스트
    ]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="좋은 제목")])
    # t2 의 임베딩이 호출되면 KeyError 가 나야 함 — 호출되지 않아야 통과
    table = {"좋은 제목": _angle_vec(0.0)}
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "y1"
    assert out[1].parent_story_id is None  # 빈 텍스트 → 매칭 skip


def test_empty_text_yesterday_skipped_too():
    today = [_ls("t1", tickers=["NVDA"], title="X")]
    prev = _snapshot(
        "2026-05-26",
        [
            _ls("y_empty", tickers=["NVDA"], title="", narrative=""),
            _ls("y_real", tickers=["NVDA"], title="X"),
        ],
    )
    table = {"X": _angle_vec(0.0)}
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "y_real"


def test_multiple_today_share_same_parent_is_allowed():
    """오늘 두 스토리가 같은 어제 parent에 연결되는 것 허용."""
    today = [
        _ls("t1", tickers=["NVDA"], title="T1"),
        _ls("t2", tickers=["NVDA"], title="T2"),
    ]
    prev = _snapshot("2026-05-26", [_ls("y1", tickers=["NVDA"], title="Y", first_seen="2026-05-25")])
    table = {"T1": _angle_vec(0.0), "T2": _angle_vec(0.0), "Y": _angle_vec(0.0)}
    out = link.link_to_previous(today, prev, embed_fn=_make_embed_fn(table))
    assert out[0].parent_story_id == "y1"
    assert out[1].parent_story_id == "y1"
