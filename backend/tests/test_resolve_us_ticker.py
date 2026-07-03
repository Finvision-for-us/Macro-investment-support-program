"""_select_us_ticker 순수 선택로직 단위테스트 (network 없음).

실제 Yahoo 검색에서 관측된 케이스(JPFP/Boyd/삼성/Northern Trust/ETF)를 fixture로 재현한다.
실행: python backend/tests/test_resolve_us_ticker.py
      또는 python -m unittest backend.tests.test_resolve_us_ticker
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.yfinance_client import _select_us_ticker


def Q(ticker, name, exchange, quote_type="EQUITY"):
    return {"ticker": ticker, "name": name, "exchange": exchange,
            "sector": "", "quote_type": quote_type}


class TestSelectUsTicker(unittest.TestCase):
    def test_jpmorgan_picks_bank_not_fund(self):
        # JPFP(펀드명) 제외 → JPM
        quotes = [
            Q("JPFP", "JPMorgan Managed Futures Plus ETF", "NASDAQ"),
            Q("JPM", "JPMorgan Chase & Co.", "NYSE"),
        ]
        self.assertEqual(_select_us_ticker("JPMorgan", quotes), "JPM")

    def test_byd_name_collision_picks_company_not_boyd(self):
        # "BYD" 질의가 Boyd Gaming(ticker BYD)에 안 걸리고 BYD Company(BYDDY)로
        quotes = [
            Q("BYD", "Boyd Gaming Corporation", "NYSE"),
            Q("BYDDY", "BYD Company Limited", "OTC Markets"),
        ]
        self.assertEqual(_select_us_ticker("BYD", quotes), "BYDDY")

    def test_samsung_skips_korea_takes_us_otc(self):
        quotes = [
            Q("005930.KS", "Samsung Electronics Co., Ltd.", "Korea"),
            Q("SSNLF", "Samsung Electronics Co., Ltd.", "OTC Markets"),
        ]
        self.assertEqual(_select_us_ticker("Samsung Electronics", quotes), "SSNLF")

    def test_us_primary_preferred_over_otc(self):
        quotes = [
            Q("TICKA", "Example Corp", "OTC Markets"),
            Q("TICKB", "Example Corp", "NYSE"),
        ]
        self.assertEqual(_select_us_ticker("Example", quotes), "TICKB")

    def test_northern_trust_not_excluded_as_fund(self):
        # 'trust'는 펀드 단어가 아니므로 정상 회사가 통과해야 함
        quotes = [Q("NTRS", "Northern Trust Corporation", "NASDAQ")]
        self.assertEqual(_select_us_ticker("Northern Trust", quotes), "NTRS")

    def test_pure_etf_excluded(self):
        quotes = [Q("SPY", "SPDR S&P 500 ETF Trust", "NYSEArca", quote_type="ETF")]
        self.assertIsNone(_select_us_ticker("S&P 500", quotes))

    def test_equity_but_fund_name_excluded(self):
        # quote_type이 EQUITY로 와도 이름이 펀드면 제외
        quotes = [Q("XXXX", "Some Index Fund", "NASDAQ", quote_type="EQUITY")]
        self.assertIsNone(_select_us_ticker("Some", quotes))

    def test_no_us_listing_returns_none(self):
        quotes = [
            Q("005930.KS", "Samsung Electronics Co., Ltd.", "Korea"),
            Q("1810.HK", "Xiaomi Corporation", "Hong Kong"),
        ]
        self.assertIsNone(_select_us_ticker("Samsung Electronics", quotes))

    def test_name_mismatch_excluded(self):
        # 이름이 전혀 안 맞으면 제외
        quotes = [Q("ZZZZ", "Totally Different Company", "NYSE")]
        self.assertIsNone(_select_us_ticker("Apple", quotes))

    def test_prefix_match_brand_vs_legal_name(self):
        # 브랜드명(Pepsi)이 법인명(PepsiCo)의 접두면 매칭되어야 함
        quotes = [Q("PEP", "PepsiCo, Inc.", "NASDAQ")]
        self.assertEqual(_select_us_ticker("Pepsi", quotes), "PEP")

    def test_prefix_no_false_positive_short_token(self):
        # 3자 이하 토큰은 접두매칭에서 제외(오탐 방지). 'ab'가 'abbott'에 접두여도 매칭 안 함.
        quotes = [Q("ABT", "Abbott Laboratories", "NYSE")]
        self.assertIsNone(_select_us_ticker("Ab", quotes))

    def test_non_list_input(self):
        self.assertIsNone(_select_us_ticker("X", None))
        self.assertIsNone(_select_us_ticker("X", []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
