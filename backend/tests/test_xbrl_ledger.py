"""SEC XBRL 원장 대조(방어선 4c) 단위테스트 — network/LLM 없음.

INDI/Wuxi 감사에서 원문 확정한 수치를 픽스처로 재현한다.
핵심 원칙: 확인 전용(원장 미존재 수치는 침묵), 라운드 값($1M 단위)은
근접 우연이 구조적이라 '정수 정확 일치'만 인정(라이브 실측으로 확정된 규칙).

실행: python backend/tests/test_xbrl_ledger.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents import xbrl_ledger as xl


FIXTURE = {"facts": {"us-gaap": {
    "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
        {"val": 174433000, "end": "2026-03-31", "fy": 2026, "fp": "Q1", "form": "10-Q"},
        {"val": 145456000, "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K"},
    ]}},
    "NetIncomeLoss": {"units": {"USD": [
        {"val": -150712000, "end": "2025-12-31", "fy": None, "fp": None, "form": "DEF 14A"},
    ]}},
    "OperatingIncomeLoss": {"units": {"USD": [
        {"val": -135423000, "end": "2023-12-31", "fy": 2023, "fp": "FY", "form": "10-K"},
    ]}},
    "ProceedsFromIssuanceOfPrivatePlacement": {"units": {"USD": [
        {"val": 150000000, "end": "2021-06-30", "fy": 2021, "fp": "Q2", "form": "10-Q"},
    ]}},
    "TinyItem": {"units": {"USD": [
        {"val": 5000, "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K"}]}},
    "SharesItem": {"units": {"shares": [
        {"val": 174433000, "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K"}]}},
}}}


def _ledger() -> xl.XbrlLedger:
    return xl.XbrlLedger("INDI", xl._build_facts(FIXTURE))


class TestBuildFacts(unittest.TestCase):

    def test_usd_only_and_min_value(self):
        """USD 단위만, $1M 미만 제외."""
        facts = xl._build_facts(FIXTURE)
        concepts = {f.concept for f in facts}
        self.assertNotIn("TinyItem", concepts)
        self.assertNotIn("SharesItem", concepts)
        self.assertEqual(len(facts), 5)

    def test_missing_fy_fp_tolerated(self):
        """DEF 14A처럼 fy/fp가 None이어도 빈 문자열로 안전 구축."""
        facts = xl._build_facts(FIXTURE)
        ni = next(f for f in facts if f.concept == "NetIncomeLoss")
        self.assertEqual(ni.fy, "")
        self.assertEqual(ni.fp, "")


class TestMatch(unittest.TestCase):

    def test_rounded_report_value_matches(self):
        """$174.4M(반올림 보고값) ↔ 174,433,000 (오차 0.019%) 일치."""
        hits = _ledger().match(174_400_000)
        self.assertTrue(hits)
        self.assertEqual(hits[0].concept, "CashAndCashEquivalentsAtCarryingValue")
        self.assertEqual(hits[0].fy, "2026")  # 최신 end 우선

    def test_negative_ledger_value_matched_by_magnitude(self):
        """손실(음수)도 보고서엔 크기로 언급 → abs 매치."""
        hits = _ledger().match(150_700_000)
        self.assertTrue(hits)
        self.assertEqual(hits[0].concept, "NetIncomeLoss")

    def test_unrelated_value_silent(self):
        self.assertEqual(_ledger().match(999_999_999), [])


class TestVerifyStatements(unittest.TestCase):

    TEXT = ("cash of $174.4 million, net loss of $150.7 million, "
            "deal consideration of approximately $135 million, "
            "convertible notes of $150 million.")

    def test_true_matches_reported(self):
        lines = xl.verify_amounts_against_ledger(self.TEXT, _ledger())
        joined = " ".join(lines)
        self.assertIn("174.4", joined)
        self.assertIn("CashAndCash", joined)
        self.assertIn("150.7", joined)

    def test_round_value_requires_exact_ledger_match(self):
        """라운드 $135M: 원장의 -135,423,000(0.31%)과 매치되면 안 됨(오탐).
        라운드 $150M: 원장에 '정확히' 150,000,000 존재 → 정당 통과."""
        lines = xl.verify_amounts_against_ledger(self.TEXT, _ledger())
        joined = " ".join(lines)
        self.assertNotIn("$135 million", joined)
        self.assertIn("$150 million", joined)
        self.assertIn("PrivatePlacement", joined)

    def test_label_fallback_when_fy_missing(self):
        """fy/fp 없는 항목은 end 날짜로 라벨 — '( ,' 빈 라벨 금지."""
        lines = xl.verify_amounts_against_ledger("net loss of $150.7 million", _ledger())
        self.assertTrue(lines)
        self.assertNotIn("( ,", lines[0])
        self.assertIn("2025-12-31", lines[0])

    def test_empty_inputs(self):
        self.assertEqual(xl.verify_amounts_against_ledger("", _ledger()), [])
        self.assertEqual(xl.verify_amounts_against_ledger("no numbers here", _ledger()), [])

    def test_statement_cap(self):
        """상한(_MAX_STATEMENTS) 초과 생성 금지."""
        many = " ".join(f"${100+i}.{i%10} million" for i in range(30))
        lines = xl.verify_amounts_against_ledger(many, _ledger())
        self.assertLessEqual(len(lines), xl._MAX_STATEMENTS)


class TestFrontendTag(unittest.TestCase):

    def test_tag_format(self):
        """'[원장 일치]' 태그 — 프론트 classify(/정합|일치/)가 초록으로 분류하는 형식."""
        lines = xl.verify_amounts_against_ledger("cash of $174.4 million", _ledger())
        self.assertTrue(lines[0].startswith("[원장 일치]"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
