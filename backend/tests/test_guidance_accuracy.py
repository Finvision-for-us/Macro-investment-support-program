"""가이던스 vs 실제 대조 엔진 단위 테스트 (순수 함수, network 없음).

pytest가 환경에 없으므로 표준 라이브러리 unittest로 작성.
실행: python backend/tests/test_guidance_accuracy.py

evaluate_guidance_accuracy: 경영진 forward_guidance를 다음 분기(P+1) 실제값과 대조.
실제 Gemini/Yahoo/network 호출은 어떤 테스트에서도 발생하지 않는다.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.gemini_guidance import (
    _qkey,
    _classify_vs_range,
    _is_single_quarter_target,
    _normalize_units,
    _is_real_earnings_row,
    evaluate_guidance_accuracy,
)


class TestHelpers(unittest.TestCase):
    def test_qkey(self):
        self.assertEqual(_qkey("2025-03-29"), (2025, 1))
        self.assertEqual(_qkey("2024-12-28"), (2024, 4))
        self.assertEqual(_qkey("2025-07-01"), (2025, 3))
        self.assertIsNone(_qkey(None))
        self.assertIsNone(_qkey("bad"))

    def test_classify_vs_range(self):
        self.assertEqual(_classify_vs_range(47.0, 46.5, 47.5), "within")
        self.assertEqual(_classify_vs_range(48.0, 46.5, 47.5), "above")
        self.assertEqual(_classify_vs_range(46.0, 46.5, 47.5), "below")
        # 편면: 상한만(이하) / 하한만(이상)
        self.assertEqual(_classify_vs_range(47.0, None, 47.5), "within")   # 47<=47.5 → 이하 충족
        self.assertEqual(_classify_vs_range(48.0, None, 47.5), "above")    # 47.5 초과
        self.assertEqual(_classify_vs_range(48.0, 46.5, None), "within")   # 46.5 이상 충족
        self.assertEqual(_classify_vs_range(46.0, 46.5, None), "below")    # 46.5 미달
        self.assertIsNone(_classify_vs_range(None, 46.5, 47.5))
        self.assertIsNone(_classify_vs_range(47.0, None, None))
        # low>high 뒤집힘 방어
        self.assertEqual(_classify_vs_range(47.0, 47.5, 46.5), "within")

    def test_is_single_quarter_target(self):
        self.assertTrue(_is_single_quarter_target("2025 Q2(3월분기)"))
        self.assertTrue(_is_single_quarter_target("March quarter"))
        self.assertFalse(_is_single_quarter_target("FY2025"))
        self.assertFalse(_is_single_quarter_target("2025 연간"))
        self.assertFalse(_is_single_quarter_target("2025 하반기"))

    def test_normalize_units(self):
        # 소수(0.744~0.754) 마진 → 실제값(74.93 %) 기준 ~100배 작음 → ×100
        self.assertEqual(_normalize_units("gross_margin", 0.744, 0.754, 74.93), (74.4, 75.4))
        # 이미 퍼센트면 그대로(ratio≈1)
        self.assertEqual(_normalize_units("gross_margin", 74.4, 75.4, 74.93), (74.4, 75.4))
        # 진짜 miss(1~3배 차이)는 건드리지 않음
        self.assertEqual(_normalize_units("gross_margin", 40.0, 42.0, 74.93), (40.0, 42.0))
        # 퍼센트 지표가 아니면 그대로 (매출)
        self.assertEqual(_normalize_units("revenue", 63.7e9, 66.3e9, 68e9), (63.7e9, 66.3e9))
        # 편면 소수 → ×100
        self.assertEqual(_normalize_units("operating_margin", None, 0.30, 33.0), (None, 30.0))
        # 실제값 없으면 그대로
        self.assertEqual(_normalize_units("gross_margin", 0.744, 0.754, None), (0.744, 0.754))
        # 매출 십억 단위(76.44) vs 실제 raw USD → ×1e9
        low, high = _normalize_units("revenue", 76.44, 79.56, 68.127e9)
        self.assertAlmostEqual(low, 76.44e9, places=2)
        self.assertAlmostEqual(high, 79.56e9, places=2)
        # 매출이 이미 raw면 그대로 (배율 1)
        self.assertEqual(_normalize_units("revenue", 63.7e9, 66.3e9, 68e9), (63.7e9, 66.3e9))

    def test_is_real_earnings_row(self):
        # 실제 발표: report_date != period_end
        self.assertTrue(_is_real_earnings_row({"period_end": "2026-01-25", "report_date": "2026-02-25"}))
        # 유령 추정행: report_date == period_end
        self.assertFalse(_is_real_earnings_row({"period_end": "2026-06-30", "report_date": "2026-06-30"}))
        # report 없음 → 판정 불가 → 제외
        self.assertFalse(_is_real_earnings_row({"period_end": "2026-06-30"}))
        # date 폴백 사용
        self.assertTrue(_is_real_earnings_row({"period_end": "2026-06-30", "date": "2026-08-01"}))


class TestEvaluate(unittest.TestCase):
    # 콜 period_end = 2024-12-28 (2024 Q4) → 대상 다음 분기 = 2025 Q1
    def _g(self, items):
        return [{"period_end": "2024-12-28", "forward_guidance": items}]

    def test_hit_within_range(self):
        g = self._g([{"metric": "gross_margin", "low": 46.5, "high": 47.5,
                      "unit": "%", "target_period": "2025 Q2"}])
        actuals = {("gross_margin", (2025, 1)): 47.05}
        r = evaluate_guidance_accuracy(g, actuals)
        self.assertEqual(r["evaluated"], 1)
        self.assertEqual(r["within"], 1)
        self.assertEqual(r["hit_rate"], 100.0)
        self.assertEqual(r["items"][0]["verdict"], "within")
        self.assertEqual(r["items"][0]["target_quarter"], "2025Q1")

    def test_above_and_below(self):
        g = self._g([
            {"metric": "gross_margin", "low": 46.5, "high": 47.5, "unit": "%", "target_period": "Q"},
            {"metric": "eps", "low": 1.9, "high": 2.0, "unit": "USD", "target_period": "Q"},
        ])
        actuals = {("gross_margin", (2025, 1)): 48.2, ("eps", (2025, 1)): 1.65}
        r = evaluate_guidance_accuracy(g, actuals)
        verdicts = {it["metric"]: it["verdict"] for it in r["items"]}
        self.assertEqual(verdicts["gross_margin"], "above")
        self.assertEqual(verdicts["eps"], "below")
        self.assertEqual(r["within"], 0)
        self.assertEqual(r["hit_rate"], 0.0)

    def test_qualitative_skipped(self):
        g = self._g([{"metric": "revenue_growth", "low": None, "high": None,
                      "unit": "%", "target_period": "Q", "verbatim": "low single digit"}])
        r = evaluate_guidance_accuracy(g, {("revenue_growth", (2025, 1)): 5.0})
        self.assertEqual(r["evaluated"], 0)

    def test_annual_target_skipped(self):
        g = self._g([{"metric": "gross_margin", "low": 46, "high": 47,
                      "unit": "%", "target_period": "FY2025 연간"}])
        r = evaluate_guidance_accuracy(g, {("gross_margin", (2025, 1)): 46.5})
        self.assertEqual(r["evaluated"], 0)

    def test_q4_wraps_to_next_year(self):
        # 9월분기(Q3) 콜 → 다음 분기 Q4
        g = [{"period_end": "2024-09-28", "forward_guidance":
              [{"metric": "gross_margin", "low": 46, "high": 47, "unit": "%", "target_period": "Q"}]}]
        r = evaluate_guidance_accuracy(g, {("gross_margin", (2024, 4)): 46.5})
        self.assertEqual(r["items"][0]["target_quarter"], "2024Q4")
        self.assertEqual(r["items"][0]["verdict"], "within")

    def test_fraction_margin_normalized_to_within(self):
        # LLM이 마진을 소수(0.744~0.754)로 뽑아도 실제값(47.05→여기선 74.93 %) 대조 시 within
        g = self._g([{"metric": "gross_margin", "low": 0.744, "high": 0.754,
                      "unit": "%", "target_period": "Q"}])
        r = evaluate_guidance_accuracy(g, {("gross_margin", (2025, 1)): 74.93})
        self.assertEqual(r["items"][0]["verdict"], "within")
        self.assertEqual(r["within"], 1)

    def test_missing_actual_skipped(self):
        g = self._g([{"metric": "gross_margin", "low": 46.5, "high": 47.5,
                      "unit": "%", "target_period": "Q"}])
        r = evaluate_guidance_accuracy(g, {})  # 실제값 없음
        self.assertEqual(r["evaluated"], 0)

    def test_empty_and_none(self):
        self.assertEqual(evaluate_guidance_accuracy([], {})["evaluated"], 0)
        self.assertEqual(evaluate_guidance_accuracy(None, None)["evaluated"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
