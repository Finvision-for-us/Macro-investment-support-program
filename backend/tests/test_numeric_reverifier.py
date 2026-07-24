"""수치 재검증(결정론) 단위테스트 — network/LLM 없음.

핵심 계약:
- 태그 구간의 수치가 '전부' 원문에서 확인될 때만 [unverified] 해제.
- 다국어 정규화 매칭: "74억 달러" ↔ "$7.4 billion", 연도, %, 통화없는 큰 수.
- 수치 없는 태그 구간은 불변(사실관계 판단은 LLM 검증 존중 — 보수성).
- 부분 확인(수치 2개 중 1개만 원문 존재)은 태그 유지.

실행: python backend/tests/test_numeric_reverifier.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents.numeric_reverifier import (
    CorpusIndex, reverify_report,
)

CORPUS = """
indie Semiconductor reported a strategic backlog of $7.4 billion as of Q3 2025.
Annual revenue was $217 million while GAAP net loss reached $143 million in 2025.
The company was founded and listed via SPAC in 2021. Gross margin was 50.2%.
Shares outstanding grew to 118,000,000 over three years.
"""


class TestCorpusIndex(unittest.TestCase):

    def setUp(self):
        self.idx = CorpusIndex(CORPUS)

    def test_korean_money_matches_english_corpus(self):
        """'74억 달러' ↔ '$7.4 billion' 교차언어 정규화 매칭."""
        self.assertTrue(self.idx.claim_confirmed("수주잔고가 74억 달러 규모로 확장"))

    def test_year_and_percent(self):
        self.assertTrue(self.idx.claim_confirmed("2021년 SPAC 상장, 마진 50.2% 기록"))

    def test_bare_large_number(self):
        self.assertTrue(self.idx.claim_confirmed("발행주식수 1억 1,800만 주"))

    def test_wrong_number_not_confirmed(self):
        self.assertFalse(self.idx.claim_confirmed("수주잔고 99억 달러"))

    def test_partial_match_rejected(self):
        """수치 2개 중 1개만 존재 → 미확인(전수 확인 원칙)."""
        self.assertFalse(self.idx.claim_confirmed("매출 2억 1,700만 달러, 순이익 9억 달러"))

    def test_no_numbers_returns_none(self):
        self.assertIsNone(self.idx.claim_confirmed("파트너십이 강화되었다"))


class TestReverifyReport(unittest.TestCase):

    def test_removes_confirmed_keeps_unconfirmed_and_factual(self):
        data = {
            "summary": ("[unverified] 수주잔고가 74억 달러로 확장되었다. "
                        "[unverified] 순손실은 1억 4,300만 달러였다. "
                        "[unverified] 99조 원 규모의 신사업. "
                        "[unverified] 경영진이 교체되었다."),
            "sections": [{"title": "재무", "content": "[unverified] 2025년 마진 50.2%."}],
            "key_findings": [{"finding": "[unverified] 주식수 1억 1,800만 주"}],
            "cross_validation": ["[unverified] 매출 2억 1,700만 달러 정체"],
        }
        out, removed, kept = reverify_report(data, CORPUS)
        # 확인된 수치 태그는 사라진다
        self.assertNotIn("[unverified] 수주잔고", out["summary"])
        self.assertIn("수주잔고가 74억 달러로 확장", out["summary"])
        self.assertNotIn("unverified", out["sections"][0]["content"])
        self.assertNotIn("unverified", out["key_findings"][0]["finding"])
        self.assertNotIn("unverified", out["cross_validation"][0])
        # 틀린 수치(99조)와 수치 없는 사실관계 태그는 유지된다
        self.assertIn("[unverified] 99조", out["summary"])
        self.assertIn("[unverified] 경영진이 교체되었다", out["summary"])
        self.assertEqual(removed, 5)
        self.assertEqual(kept, 2)

    def test_double_bracket_variant_handled(self):
        data = {"summary": "[[unverified]] 2021년 상장"}
        out, removed, _ = reverify_report(data, CORPUS)
        self.assertEqual(removed, 1)
        self.assertNotIn("unverified", out["summary"])

    def test_no_corpus_noop(self):
        data = {"summary": "[unverified] 2021년"}
        out, removed, kept = reverify_report(data, "")
        self.assertEqual((removed, kept), (0, 0))
        self.assertIn("[unverified]", out["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
