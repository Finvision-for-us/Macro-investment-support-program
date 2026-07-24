"""llm_client 사용량 전수 집계 단위테스트 — network 없음.

핵심 계약:
- 모델별 입력/출력/사고 토큰이 누적되고, 사고토큰은 출력 단가로 과금된다.
- 미등록 모델은 lite 단가로 근사(과대청구 방지 방향이 아니라 근사임을 명시).
- reset_usage로 잡 간 누적이 끊긴다.

실행: python backend/tests/test_llm_usage.py
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research import llm_client as lc


def _resp(inp, out, think):
    return SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=inp, candidates_token_count=out,
        thoughts_token_count=think))


class TestUsageAccounting(unittest.TestCase):

    def setUp(self):
        lc.reset_usage()

    def tearDown(self):
        lc.reset_usage()

    def test_accumulates_per_model(self):
        lc._record_usage("gemini-3.5-flash", _resp(10_000, 2_000, 1_500))
        lc._record_usage("gemini-3.5-flash", _resp(5_000, 1_000, 500))
        lc._record_usage("gemini-3.1-flash-lite", _resp(100_000, 8_000, 0))
        u = lc.get_usage()
        self.assertEqual(u["gemini-3.5-flash"],
                         {"input": 15_000, "output": 3_000, "thinking": 2_000, "calls": 2})
        self.assertEqual(u["gemini-3.1-flash-lite"]["calls"], 1)
        self.assertEqual(lc.total_tokens(), 15_000 + 3_000 + 2_000 + 100_000 + 8_000)

    def test_cost_thinking_billed_as_output(self):
        """3.5-flash: $1.5/M 입력, $9/M 출력(사고 포함) — 수기 계산과 일치해야 한다."""
        lc._record_usage("gemini-3.5-flash", _resp(1_000_000, 100_000, 100_000))
        expected = 1.50 + (0.1 + 0.1) * 9.00  # $1.5 + $1.8
        self.assertAlmostEqual(lc.estimated_cost_usd(), expected, places=6)

    def test_unknown_model_uses_default_price(self):
        lc._record_usage("gemini-99-experimental", _resp(1_000_000, 0, 0))
        self.assertAlmostEqual(lc.estimated_cost_usd(), 0.25, places=6)

    def test_reset_clears(self):
        lc._record_usage("gemini-3.5-flash", _resp(100, 100, 0))
        lc.reset_usage()
        self.assertEqual(lc.get_usage(), {})
        self.assertEqual(lc.estimated_cost_usd(), 0.0)

    def test_no_usage_metadata_is_safe(self):
        lc._record_usage("gemini-3.5-flash", SimpleNamespace())  # usage_metadata 없음
        self.assertEqual(lc.get_usage(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
