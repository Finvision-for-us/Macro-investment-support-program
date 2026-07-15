"""Synthesizer confidence 방어 단위테스트 — network/LLM 없음.

LLM이 confidence를 'none' 등 유효하지 않은 값으로 반환하면 `ConfidenceLevel(...)`
직접 생성이 ValueError를 던져 리서치 전체가 폴백(0결과)되던 버그를 방지한다.

실행: python backend/tests/test_synthesizer_confidence.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents.synthesizer import _coerce_confidence
from app.deep_research.models import ConfidenceLevel, KeyFinding


class TestCoerceConfidence(unittest.TestCase):

    def test_invalid_values_default_to_medium(self):
        for bad in ["none", "", "n/a", "unknown", "garbage", None, "  ", "null"]:
            self.assertEqual(_coerce_confidence(bad), ConfidenceLevel.MEDIUM,
                             f"{bad!r} → MEDIUM 기대")

    def test_valid_values(self):
        self.assertEqual(_coerce_confidence("high"), ConfidenceLevel.HIGH)
        self.assertEqual(_coerce_confidence("HIGH"), ConfidenceLevel.HIGH)
        self.assertEqual(_coerce_confidence(" low "), ConfidenceLevel.LOW)
        self.assertEqual(_coerce_confidence("medium"), ConfidenceLevel.MEDIUM)

    def test_korean_and_abbrev(self):
        self.assertEqual(_coerce_confidence("높음"), ConfidenceLevel.HIGH)
        self.assertEqual(_coerce_confidence("보통"), ConfidenceLevel.MEDIUM)
        self.assertEqual(_coerce_confidence("낮음"), ConfidenceLevel.LOW)
        self.assertEqual(_coerce_confidence("H"), ConfidenceLevel.HIGH)

    def test_keyfinding_does_not_crash_on_none(self):
        """confidence='none'이어도 KeyFinding 생성이 예외를 던지지 않는다."""
        kf = KeyFinding(finding="x", confidence=_coerce_confidence("none"), sources=[])
        self.assertEqual(kf.confidence, ConfidenceLevel.MEDIUM)


class TestCriticConfidenceCoercion(unittest.TestCase):
    """Critic이 LLM confidence를 문자열로 받아도 크래시하지 않는다(평가 폴백 방지)."""

    def test_coerce(self):
        from app.deep_research.agents.critic import _coerce_float_confidence as f
        self.assertAlmostEqual(f("high"), 0.9)
        self.assertAlmostEqual(f("low"), 0.3)
        self.assertAlmostEqual(f("0.7"), 0.7)
        self.assertAlmostEqual(f(0.8), 0.8)
        self.assertAlmostEqual(f("garbage"), 0.5)
        self.assertAlmostEqual(f(None), 0.5)
        self.assertAlmostEqual(f(1.5), 1.0)   # 클램프
        self.assertAlmostEqual(f(-0.2), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
