"""M3.5 Day 1~2 ``macro.themes`` 단위 테스트.

LLM/Gemini 의존성 회피 위해 ``embed_fn`` / ``name_fn`` 모두 주입.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.causal.schema import Story
from src.macro import themes


def _story(
    sid: str,
    *,
    title: str = "T",  # 명시적으로 ""을 넘기는 테스트가 있으므로 폴백 X
    narrative: str = "",
    tickers: list[str] | None = None,
    score: float = 0.5,
    direction: str = "uncertain",
) -> Story:
    return Story(
        id=sid,
        event_ids=[f"e-{sid}"],
        title=title,
        narrative_short=narrative,
        narrative_long="",
        direction=direction,  # type: ignore[arg-type]
        confidence=0.7,
        affected_tickers=tickers or [],
        aggregated_impact=score,
    )


def _angle_vec(theta_rad: float) -> list[float]:
    return [math.cos(theta_rad), math.sin(theta_rad)]


def _make_embed_fn(table: dict[str, list[float]]):
    def fn(texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            if t not in table:
                raise KeyError(f"테스트 임베딩 테이블에 없는 텍스트: {t!r}")
            rows.append(table[t])
        return np.array(rows, dtype=np.float32)

    return fn


def _stub_name(stories: list[Story]) -> tuple[str, str]:
    # 결정론적 폴백: 첫 스토리 제목 + 개수
    return f"테마({len(stories)}개)", f"{stories[0].title} 외"


# ----- cluster_themes ------------------------------------------------------


def test_empty_input_returns_empty():
    assert themes.cluster_themes([]) == []


def test_single_story_kept_as_solo_theme():
    s = _story("s1", title="A", tickers=["NVDA"])
    out = themes.cluster_themes([s], embed_fn=_make_embed_fn({"A": _angle_vec(0)}))
    assert len(out) == 1 and out[0][0].id == "s1"


def test_empty_text_stories_excluded():
    s = _story("s1", title="", narrative="")
    out = themes.cluster_themes([s], embed_fn=_make_embed_fn({}))
    assert out == []


def test_high_similarity_merged_into_one_cluster():
    s1 = _story("s1", title="A", tickers=["NVDA"])
    s2 = _story("s2", title="B", tickers=["AVGO"])
    # 같은 각도 → cos sim = 1.0 → 묶임
    table = {"A": _angle_vec(0), "B": _angle_vec(0)}
    out = themes.cluster_themes([s1, s2], embed_fn=_make_embed_fn(table))
    assert len(out) == 1
    assert {s.id for s in out[0]} == {"s1", "s2"}


def test_low_similarity_kept_separate():
    s1 = _story("s1", title="A", tickers=["NVDA"])
    s2 = _story("s2", title="B", tickers=["JPM"])
    # 90도 → cos sim 0 → 별개
    table = {"A": _angle_vec(0), "B": _angle_vec(math.pi / 2)}
    out = themes.cluster_themes([s1, s2], embed_fn=_make_embed_fn(table))
    assert len(out) == 2


def test_transitive_clustering_via_union_find():
    """A~B (0.99), B~C (0.99), A~C (낮음) → 셋 다 한 클러스터 (transitivity)."""
    s1 = _story("s1", title="A", score=0.5)
    s2 = _story("s2", title="B", score=0.3)
    s3 = _story("s3", title="C", score=0.7)
    # A 0°, B 8°, C 16° — 인접 쌍은 cos≈0.99, A↔C 는 cos≈0.96 (>0.70 라 어차피 묶임)
    # 진짜 transitivity 검증을 위해 threshold 를 0.985 로 빡빡하게 잡아 A↔C 단독으로는 안 묶이게
    table = {
        "A": _angle_vec(0),
        "B": _angle_vec(math.radians(8)),
        "C": _angle_vec(math.radians(16)),
    }
    out = themes.cluster_themes(
        [s1, s2, s3], sim_threshold=0.985, embed_fn=_make_embed_fn(table)
    )
    assert len(out) == 1
    assert {s.id for s in out[0]} == {"s1", "s2", "s3"}


def test_min_size_filter_drops_solos():
    s1 = _story("s1", title="A")
    s2 = _story("s2", title="B")
    s3 = _story("s3", title="C")  # 단독
    # A~B 묶임 (cos=1), C 단독 (90도)
    table = {
        "A": _angle_vec(0),
        "B": _angle_vec(0),
        "C": _angle_vec(math.pi / 2),
    }
    out = themes.cluster_themes(
        [s1, s2, s3], min_size=2, embed_fn=_make_embed_fn(table)
    )
    assert len(out) == 1
    assert {s.id for s in out[0]} == {"s1", "s2"}


def test_clusters_sorted_by_score_desc():
    """큰 score 합 클러스터가 먼저."""
    big1 = _story("big1", title="X", score=0.9)
    big2 = _story("big2", title="Y", score=0.8)
    small = _story("small", title="Z", score=0.2)
    # X~Y 묶임, Z 단독
    table = {
        "X": _angle_vec(0),
        "Y": _angle_vec(0),
        "Z": _angle_vec(math.pi / 2),
    }
    out = themes.cluster_themes(
        [small, big1, big2], embed_fn=_make_embed_fn(table)
    )
    # big1+big2 (점수합 1.7) 이 small (0.2) 보다 앞
    assert {s.id for s in out[0]} == {"big1", "big2"}
    assert {s.id for s in out[1]} == {"small"}


# ----- build_themes (with naming + aggregation) ---------------------------


def test_build_themes_aggregates_score_and_tickers():
    s1 = _story("s1", title="A", tickers=["NVDA", "AVGO"], score=0.6, direction="positive")
    s2 = _story("s2", title="B", tickers=["AVGO", "MSFT"], score=0.4, direction="positive")
    table = {"A": _angle_vec(0), "B": _angle_vec(0)}
    out = themes.build_themes(
        [s1, s2], embed_fn=_make_embed_fn(table), name_fn=_stub_name
    )
    assert len(out) == 1
    t = out[0]
    assert t.aggregate_score == pytest.approx(1.0, abs=1e-4)
    assert set(t.affected_tickers) == {"NVDA", "AVGO", "MSFT"}
    assert t.affected_tickers[0] == "NVDA"  # 등장 순서 유지
    assert t.direction == "positive"
    assert t.story_ids == ["s1", "s2"]
    assert t.name.startswith("테마(")


def test_build_themes_direction_majority_vote():
    s1 = _story("s1", title="A", direction="positive")
    s2 = _story("s2", title="B", direction="positive")
    s3 = _story("s3", title="C", direction="negative")
    table = {"A": _angle_vec(0), "B": _angle_vec(0), "C": _angle_vec(0)}
    out = themes.build_themes(
        [s1, s2, s3], embed_fn=_make_embed_fn(table), name_fn=_stub_name
    )
    assert out[0].direction == "positive"


def test_build_themes_returns_empty_for_no_text():
    s = _story("s1", title="", narrative="")
    out = themes.build_themes(
        [s], embed_fn=_make_embed_fn({}), name_fn=_stub_name
    )
    assert out == []


def test_name_theme_fallback_on_llm_failure(monkeypatch):
    """LLM 호출이 예외 던지면 첫 스토리 제목 폴백."""

    def boom(_):
        raise RuntimeError("gemini quota exceeded")

    monkeypatch.setattr(themes, "_call", boom)
    s = _story("s1", title="아주 긴 이상한 제목이지만 폴백 동작 확인용입니다")
    name, desc = themes.name_theme([s])
    assert name.startswith("아주 긴")
    assert desc == ""


# ----- Theme model ---------------------------------------------------------


def test_theme_model_serializes_json():
    t = themes.Theme(
        id="abc",
        name="테스트",
        description="설명",
        story_ids=["s1"],
        aggregate_score=0.5,
    )
    s = t.model_dump_json()
    assert "테스트" in s
    assert '"direction":"uncertain"' in s


# ----- 정기 공시 필터 + tier 승격 (신규) -----------------------------------


def test_is_routine_filing_flags_sec_forms():
    assert themes.is_routine_filing(
        _story("s", title="IIOT-OXYS, Inc. (ITOX), 2026년 2분기 10-Q 분기 보고서 제출")
    )
    assert themes.is_routine_filing(
        _story("s", title="GridAI Technologies Corp. (GRDX), S-1/A 수정 신고서 제출")
    )
    assert themes.is_routine_filing(
        _story("s", title="ProPhase Labs, Inc. (PRPH), 10-Q/A 수정 보고서 제출")
    )


def test_is_routine_filing_ignores_real_news():
    # 폼 토큰 없음 → 뉴스
    assert not themes.is_routine_filing(
        _story("s", title="Rocket Lab, 80억 달러 규모 Iridium 인수 발표와 주가 급등의 명암")
    )
    assert not themes.is_routine_filing(
        _story("s", title="AMD, Helios 시스템과 MI400으로 NVDA의 강력한 대항마 부상")
    )


def test_build_themes_excludes_routine_filings():
    filing = _story("f", title="ACME Inc. (ACME), 2026년 2분기 10-Q 보고서 제출", score=0.9)
    news = _story("n", title="빅딜 인수 발표", narrative="설명", score=0.3)
    table = {
        themes._story_text(filing): _angle_vec(0),
        themes._story_text(news): _angle_vec(math.pi / 2),
    }
    out = themes.build_themes([filing, news], embed_fn=_make_embed_fn(table), name_fn=_stub_name)
    ids = {sid for t in out for sid in t.story_ids}
    assert "f" not in ids  # 정기 공시 제외
    assert "n" in ids


def test_single_high_impact_story_promoted_to_headline_with_desc():
    s = _story("s1", title="대형 M&A 발표", narrative="80억 달러 인수", score=0.9)
    out = themes.build_themes(
        [s], embed_fn=_make_embed_fn({themes._story_text(s): _angle_vec(0)}), name_fn=_stub_name
    )
    assert len(out) == 1
    assert out[0].tier == "headline"          # 단독이어도 headline 승격
    assert out[0].description == "80억 달러 인수"  # narrative_short 재사용 (빈 설명 방지)


def test_headline_by_peak_impact_not_by_count():
    """단독 고임팩트(0.9) 스토리가 저임팩트 2건 뭉치(0.2+0.2)보다 헤드라인."""
    hot = _story("hot", title="초대형 뉴스", narrative="x", score=0.9)
    a = _story("a", title="AA", narrative="x", score=0.2)
    b = _story("b", title="BB", narrative="x", score=0.2)
    table = {
        themes._story_text(a): _angle_vec(0),
        themes._story_text(b): _angle_vec(0),   # a,b 묶임
        themes._story_text(hot): _angle_vec(math.pi / 2),  # hot 단독
    }
    out = themes.build_themes([hot, a, b], embed_fn=_make_embed_fn(table), name_fn=_stub_name)
    headline = next(t for t in out if t.tier == "headline")
    assert headline.story_ids == ["hot"]
