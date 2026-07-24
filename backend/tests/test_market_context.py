"""시장 수급 스냅샷 포매터 테스트 — network/LLM 없음.

핵심 계약:
- yfinance 비율 필드(소수) → % 표기, 큰 금액 → $B/M 단위.
- 값 없는 항목은 줄 자체를 생략 (None/누락 fail-soft).
- 데이터 기준일·출처·지연 데이터 명시 (정직성).
- 실데이터가 하나도 없으면 has_market_data() False → 문서 미생성 판단.

실행: python backend/tests/test_market_context.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.sources.market_context import (
    build_market_snapshot_text, has_market_data,
)

_INFO = {
    "longName": "indie Semiconductor, Inc.",
    "currentPrice": 4.85,
    "marketCap": 1_050_000_000,
    "fiftyTwoWeekLow": 1.88,
    "fiftyTwoWeekHigh": 7.16,
    "sharesOutstanding": 218_000_000,
    "floatShares": 190_000_000,
    "sharesShort": 57_900_000,
    "sharesShortPriorMonth": 55_000_000,
    "shortPercentOfFloat": 0.3049,
    "shortRatio": 6.2,
    "targetMeanPrice": 6.75,
    "targetLowPrice": 4.0,
    "targetHighPrice": 9.0,
    "numberOfAnalystOpinions": 8,
    "recommendationKey": "buy",
    "recommendationMean": 2.1,
}

_REC = [
    {"period": "2026-07-01", "strongBuy": 3, "buy": 4, "hold": 2,
     "sell": 0, "strongSell": 0},
    {"period": "2026-06-01", "strongBuy": 3, "buy": 3, "hold": 3,
     "sell": 0, "strongSell": 0},
]


class TestSnapshot(unittest.TestCase):

    def test_full_snapshot(self):
        text = build_market_snapshot_text("INDI", _INFO, _REC,
                                          as_of="2026-07-20")
        self.assertIn("indie Semiconductor, Inc. (INDI)", text)
        self.assertIn("데이터 기준일: 2026-07-20", text)
        self.assertIn("집계·지연 데이터", text)
        # 비율 소수 → %
        self.assertIn("유동주식 대비 공매도 비율: 30.49%", text)
        # 금액 단위
        self.assertIn("시가총액: $1.05B", text)
        self.assertIn("목표주가 평균: $6.75", text)
        self.assertIn("목표주가 범위: $4.00 ~ $9.00", text)
        self.assertIn("공매도 잔고: 57,900,000", text)
        self.assertIn("커버리지 애널리스트 수: 8명", text)
        self.assertIn("종합 추천: 매수 (평균 2.1", text)
        # Finnhub 트렌드
        self.assertIn("2026-07-01: 적극매수 3 / 매수 4 / 중립 2", text)
        self.assertTrue(has_market_data(text))

    def test_missing_fields_and_empty_sections_omitted(self):
        """값 없는 항목은 줄 생략, 항목 없는 섹션은 헤더째 생략."""
        text = build_market_snapshot_text(
            "XXXX", {"currentPrice": 10.0}, [], as_of="2026-07-20")
        self.assertIn("현재가: $10.00", text)
        self.assertNotIn("공매도 잔고", text)
        self.assertNotIn("[공매도 수급]", text)          # 빈 섹션 헤더 없음
        self.assertNotIn("[애널리스트 컨센서스]", text)
        self.assertNotIn("[추천 트렌드", text)
        self.assertNotIn("목표주가 평균", text)
        self.assertTrue(has_market_data(text))

    def test_empty_has_no_data(self):
        text = build_market_snapshot_text("XXXX", {}, [], as_of="2026-07-20")
        self.assertFalse(has_market_data(text))
        self.assertNotIn("[주가·규모]", text)


class TestQuoteSummaryFlatten(unittest.TestCase):
    """quoteSummary 중첩 {"raw": x} 응답 → 평탄 dict (mock, 네트워크 없음)."""

    def test_flatten_raw_values(self):
        from unittest.mock import patch
        from app.deep_research.sources.market_context import MarketContextSource
        qs = {
            "financialData": {
                "currentPrice": {"raw": 4.85, "fmt": "4.85"},
                "targetMeanPrice": {"raw": 6.75},
                "recommendationKey": "buy",          # 문자열은 래핑 없음
            },
            "defaultKeyStatistics": {
                "shortPercentOfFloat": {"raw": 0.3049},
                "sharesShort": {},                    # 빈 dict → 제외
            },
            "summaryDetail": {"marketCap": {"raw": 1_050_000_000}},
            "quoteType": {"longName": "indie Semiconductor, Inc."},
        }
        with patch("app.services.yfinance_client._yf_quoteSummary",
                   return_value=qs):
            flat = MarketContextSource._yf_info("INDI")
        self.assertEqual(flat["currentPrice"], 4.85)
        self.assertEqual(flat["recommendationKey"], "buy")
        self.assertEqual(flat["shortPercentOfFloat"], 0.3049)
        self.assertEqual(flat["longName"], "indie Semiconductor, Inc.")
        self.assertNotIn("sharesShort", flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
