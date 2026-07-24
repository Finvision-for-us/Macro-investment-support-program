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
    # 2026-07-20 INDI 감사 오탐 3건 재현용 원장 항목
    "InterestPaidNet": {"units": {"USD": [
        {"val": 15100000, "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K"}]}},
    "OtherAccruedLiabilities": {"units": {"USD": [
        {"val": 11100000, "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K"}]}},
    "AmortizationOfIntangibleAssets": {"units": {"USD": [
        {"val": 27800000, "end": "2024-12-31", "fy": 2024, "fp": "FY", "form": "10-K"}]}},
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
        self.assertEqual(len(facts), 8)

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


class TestConceptClaimMatching(unittest.TestCase):
    """개념-주장 범주 일치 — 2026-07-20 INDI 감사 오탐 3건의 재발 방지.

    값만 일치하는 무관 개념('가짜 신뢰')은 침묵, 범주가 맞으면 통과.
    """

    def test_audit_false_match_1_nongaap_loss_vs_interest(self):
        """Non-GAAP 순손실 $15.1M ↔ InterestPaidNet — 감사 오탐 1 재현."""
        lines = xl.verify_amounts_against_ledger(
            "Non-GAAP net loss of $15.1 million for the year", _ledger())
        self.assertEqual(lines, [])  # Non-GAAP은 원장에 없는 게 정상 → 침묵

    def test_gaap_loss_wrong_concept_still_silent(self):
        """GAAP 표현이어도 순손실 주장 ↔ 이자지급 개념은 범주 불일치 → 침묵."""
        lines = xl.verify_amounts_against_ledger(
            "net loss of $15.1 million in fiscal 2025", _ledger())
        self.assertEqual(lines, [])

    def test_interest_claim_matches_interest_concept(self):
        """같은 값이라도 '이자' 주장이면 InterestPaidNet 매치는 정당."""
        lines = xl.verify_amounts_against_ledger(
            "interest paid of $15.1 million", _ledger())
        self.assertTrue(lines)
        self.assertIn("InterestPaidNet", lines[0])

    def test_audit_false_match_2_contextless_value_silent(self):
        """무맥락 $11.1M ↔ OtherAccruedLiabilities — 감사 오탐 2 재현.
        문맥에 범주 신호가 없으면 판단 불가 → 침묵."""
        lines = xl.verify_amounts_against_ledger(
            "규모는 총 $11.1 million 수준으로 알려졌다", _ledger())
        self.assertEqual(lines, [])

    def test_accrued_claim_matches_liabilities(self):
        lines = xl.verify_amounts_against_ledger(
            "미지급 부채 $11.1 million 계상", _ledger())
        self.assertTrue(lines)
        self.assertIn("OtherAccruedLiabilities", lines[0])

    def test_audit_false_match_3_restructuring_vs_amortization(self):
        """구조조정 주장 $27.8M ↔ 2024 무형자산 상각 — 감사 오탐 3 재현."""
        lines = xl.verify_amounts_against_ledger(
            "restructuring charges of $27.8 million", _ledger())
        self.assertEqual(lines, [])

    def test_amortization_claim_matches(self):
        lines = xl.verify_amounts_against_ledger(
            "무형자산 상각비 $27.8 million 반영", _ledger())
        self.assertTrue(lines)
        self.assertIn("Amortization", lines[0])


class TestPeriodMatching(unittest.TestCase):
    """기간-주장 매칭 — 문서 §5.1: 개념이 맞아도 회계연도/분기가 틀리면 오판.

    같은 값·같은 개념이라도 주장이 명시한 기간과 원장 기간이 어긋나면 기각.
    주장에 기간 신호가 없으면 종전대로 최신 항목 우선(과잉 기각 방지).
    """

    def test_year_and_quarter_match_confirmed(self):
        """'2026년 1분기 현금 1억 7440만 달러' → fy2026 Q1 현금과 일치."""
        lines = xl.verify_amounts_against_ledger(
            "2026년 1분기 현금 및 현금성자산 1억 7440만 달러", _ledger())
        self.assertTrue(lines)
        self.assertIn("CashAndCash", lines[0])
        self.assertIn("2026 Q1", lines[0])

    def test_wrong_year_rejected(self):
        """같은 값(174.4M)이 fy2026 항목뿐인데 '2025년' 주장 → 기간 불일치 기각."""
        lines = xl.verify_amounts_against_ledger(
            "2025년 현금 1억 7440만 달러 보유", _ledger())
        self.assertEqual(lines, [])

    def test_wrong_quarter_rejected(self):
        """fy2026 현금 항목은 Q1인데 '4분기' 주장 → 분기 불일치 기각."""
        lines = xl.verify_amounts_against_ledger(
            "2026년 4분기 현금 1억 7440만 달러", _ledger())
        self.assertEqual(lines, [])

    def test_operating_loss_year_match(self):
        """'2023년 영업손실 1억 3540만 달러' → OperatingIncomeLoss fy2023 일치."""
        lines = xl.verify_amounts_against_ledger(
            "2023년 영업손실 1억 3540만 달러 기록", _ledger())
        self.assertTrue(lines)
        self.assertIn("OperatingIncomeLoss", lines[0])

    def test_operating_loss_wrong_year_rejected(self):
        """감사 패턴: 개념(영업손실)은 맞지만 연도(2026)가 원장(2023)과 불일치 → 기각."""
        lines = xl.verify_amounts_against_ledger(
            "2026년 영업손실 1억 3540만 달러 기록", _ledger())
        self.assertEqual(lines, [])

    def test_no_period_signal_keeps_legacy_behavior(self):
        """기간 신호 없으면 종전대로 확인(과잉 기각 없음)."""
        lines = xl.verify_amounts_against_ledger("cash of $174.4 million", _ledger())
        self.assertTrue(lines)
        self.assertIn("CashAndCash", lines[0])

    def test_year_beyond_2039_still_captured(self):
        """스모크 회귀: 2040+ 오연도가 '연도 신호 없음'으로 새면 기간 무검사됨.
        연도 정규식은 20xx 전체를 잡아야 한다."""
        y, q = xl._claim_period("2099년 1분기 영업손실 1억 3540만 달러")
        self.assertIn("2099", y)
        lines = xl.verify_amounts_against_ledger(
            "2099년 영업손실 1억 3540만 달러", _ledger())
        self.assertEqual(lines, [])


class TestFrontendTag(unittest.TestCase):

    def test_tag_format(self):
        """'[원장 일치]' 태그 — 프론트 classify(/정합|일치/)가 초록으로 분류하는 형식."""
        lines = xl.verify_amounts_against_ledger("cash of $174.4 million", _ledger())
        self.assertTrue(lines[0].startswith("[원장 일치]"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
