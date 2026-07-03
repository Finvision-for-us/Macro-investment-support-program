"""earnings_analyzer 순수 함수 단위 테스트.

외부 API/DB 없이 결정론적으로 검증 가능한 계산 로직을 커버한다.
이 모듈(2033줄)에 대한 첫 회귀 방어선.
"""
from app.services.earnings_analyzer import (
    _classify,
    _compute_statistics,
    _find_matching_key,
    _pearson_correlation,
)


class TestClassify:
    def test_none_is_unknown(self):
        assert _classify(None) == "Unknown"

    def test_beat_above_threshold(self):
        assert _classify(2.01) == "Beat"
        assert _classify(50.0) == "Beat"

    def test_miss_below_threshold(self):
        assert _classify(-2.01) == "Miss"
        assert _classify(-50.0) == "Miss"

    def test_meet_within_band(self):
        assert _classify(0.0) == "Meet"
        assert _classify(1.9) == "Meet"
        assert _classify(-1.9) == "Meet"

    def test_boundaries_are_meet(self):
        # 경계값 ±2.0은 Beat/Miss가 아니라 Meet (strict 부등호)
        assert _classify(2.0) == "Meet"
        assert _classify(-2.0) == "Meet"


class TestPearsonCorrelation:
    def test_insufficient_data_returns_zero(self):
        assert _pearson_correlation([]) == (0.0, 0.0)
        assert _pearson_correlation([(1, 2)]) == (0.0, 0.0)
        assert _pearson_correlation([(1, 2), (3, 4)]) == (0.0, 0.0)

    def test_perfect_positive(self):
        corr, r_sq = _pearson_correlation([(1, 1), (2, 2), (3, 3)])
        assert corr == 1.0
        assert r_sq == 1.0

    def test_perfect_negative(self):
        corr, r_sq = _pearson_correlation([(1, 3), (2, 2), (3, 1)])
        assert corr == -1.0
        assert r_sq == 1.0

    def test_zero_variance_returns_zero(self):
        # x가 상수면 std_x=0 → (0.0, 0.0)
        assert _pearson_correlation([(5, 1), (5, 2), (5, 3)]) == (0.0, 0.0)


class TestFindMatchingKey:
    def test_empty_date_returns_none(self):
        assert _find_matching_key({"2026-01-01": {}}, "") is None

    def test_invalid_format_returns_none(self):
        assert _find_matching_key({"2026-01-01": {}}, "not-a-date") is None

    def test_exact_match(self):
        assert _find_matching_key({"2026-01-01": {}}, "2026-01-01") == "2026-01-01"

    def test_within_tolerance(self):
        # 10일 이내는 같은 분기로 인식
        assert _find_matching_key({"2026-01-01": {}}, "2026-01-08") == "2026-01-01"

    def test_outside_tolerance_returns_none(self):
        assert _find_matching_key({"2026-01-01": {}}, "2026-02-01") is None

    def test_skips_malformed_existing_keys(self):
        merged = {"garbage": {}, "2026-01-05": {}}
        assert _find_matching_key(merged, "2026-01-01") == "2026-01-05"


class TestComputeStatistics:
    def test_empty_history(self):
        stats = _compute_statistics([])
        assert stats["Beat"] == {"count": 0}
        assert stats["Meet"] == {"count": 0}
        assert stats["Miss"] == {"count": 0}

    def test_beat_aggregation(self):
        history = [
            {"category": "Beat", "reaction_1d_change": 2.0, "post_3d_change": 3.0,
             "post_5d_change": 4.0, "pre_3d_change": 1.0},
            {"category": "Beat", "reaction_1d_change": -1.0, "post_3d_change": 1.0,
             "post_5d_change": 2.0, "pre_3d_change": 0.0},
        ]
        beat = _compute_statistics(history)["Beat"]
        assert beat["count"] == 2
        assert beat["avg_reaction_1d"] == 0.5      # (2.0 + -1.0)/2
        assert beat["max_reaction"] == 2.0
        assert beat["min_reaction"] == -1.0
        assert beat["up_probability"] == 50.0      # 1/2 양수

    def test_unknown_category_ignored(self):
        history = [{"category": "Unknown", "reaction_1d_change": 5.0}]
        stats = _compute_statistics(history)
        assert stats["Beat"]["count"] == 0
        assert stats["Meet"]["count"] == 0
        assert stats["Miss"]["count"] == 0

    def test_none_values_excluded_from_avg(self):
        history = [
            {"category": "Meet", "reaction_1d_change": 4.0},
            {"category": "Meet", "reaction_1d_change": None},
        ]
        meet = _compute_statistics(history)["Meet"]
        assert meet["count"] == 2
        assert meet["avg_reaction_1d"] == 4.0      # None 제외 평균
        assert meet["up_probability"] == 100.0     # 유효값 1개, 양수
