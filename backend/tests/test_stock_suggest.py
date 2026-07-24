"""통합 종목 검색(큐레이트+SEC 유니버스+한글 로마자) 단위테스트 — 네트워크 없음.

핵심 계약:
- 로마자 변환: 인디→indi, 인텔→intel(비한글 보존).
- 유니버스 티어: 정확 티커(0) < 정확 이름(1) < 접두(2) < 부분(4) < 퍼지(5).
- 통합 병합: 유니버스 정확/접두 매치가 큐레이트 퍼지 노이즈보다 위.
- 한글 브리지: '인디' → 로마자 'indi' → INDI(정확 티커).

실행: python backend/tests/test_stock_suggest.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import stock_dictionary as sd

_FIXTURE = [
    ("INDI", "indie Semiconductor, Inc."),
    ("INDV", "Indivior PLC"),
    ("NVDA", "NVIDIA CORP"),
    ("TSLA", "Tesla, Inc."),
    ("PLTR", "Palantir Technologies Inc."),
]


def _inject_universe():
    sd._universe_loaded = True
    sd._uni_entries = []
    sd._uni_name_keys = []
    sd._uni_by_ticker = {}
    sd._uni_word_bucket = {}
    for ticker, name in _FIXTURE:
        e = {"ticker": ticker, "name": name}
        nkey = sd._normalize(name)
        sd._uni_by_ticker[ticker] = e
        sd._uni_entries.append(e)
        sd._uni_name_keys.append(nkey)
        for w in sd._sig_words(name):
            sd._uni_word_bucket.setdefault(w[0], []).append((w, e))


class TestRomanize(unittest.TestCase):

    def test_known_cases(self):
        self.assertEqual(sd.romanize_hangul("인디"), "indi")
        self.assertEqual(sd.romanize_hangul("인텔"), "intel")

    def test_non_hangul_preserved(self):
        self.assertEqual(sd.romanize_hangul("인디 semi"), "indi semi")
        self.assertEqual(sd.romanize_hangul("INDI"), "INDI")

    def test_has_hangul(self):
        self.assertTrue(sd._has_hangul("인디"))
        self.assertFalse(sd._has_hangul("indie"))
        self.assertTrue(sd._has_hangul("indie 반도체"))


class TestUniverseTiers(unittest.TestCase):

    def setUp(self):
        _inject_universe()

    def tearDown(self):
        sd._universe_loaded = False
        sd._uni_entries = []
        sd._uni_name_keys = []
        sd._uni_by_ticker = {}
        sd._uni_word_bucket = {}

    def test_exact_ticker_tier0(self):
        out = sd._search_universe_scored("INDI")
        self.assertEqual(out[0][0], 0)
        self.assertEqual(out[0][1]["ticker"], "INDI")

    def test_name_prefix_tier2(self):
        out = sd._search_universe_scored("indie")
        tickers = [e["ticker"] for _, e in out]
        self.assertIn("INDI", tickers)
        self.assertNotIn("INDV", tickers)  # 'indivior'는 'indie' 접두 아님

    def test_substring_needs_len3(self):
        out2 = sd._search_universe_scored("in")   # len2 → 접두만
        out_semi = sd._search_universe_scored("semiconductor")  # 부분문자열
        self.assertIn("INDI", [e["ticker"] for _, e in out_semi])
        # len2 부분문자열은 노이즈 방지로 제외 (접두 매치만)
        self.assertTrue(all(tier <= 2 for tier, _ in out2))

    def test_typo_fuzzy(self):
        """'nvida'(오타) → NVIDIA 퍼지 매치."""
        out = sd._search_universe_scored("nvida")
        self.assertIn("NVDA", [e["ticker"] for _, e in out])


class TestSearchSuggestMerge(unittest.TestCase):

    def setUp(self):
        _inject_universe()

    def tearDown(self):
        sd._universe_loaded = False
        sd._uni_entries = []
        sd._uni_name_keys = []
        sd._uni_by_ticker = {}
        sd._uni_word_bucket = {}

    def test_korean_bridge_finds_indi(self):
        """핵심: '인디'(큐레이트에 없음) → 로마자 브리지로 INDI 최상위."""
        out = sd.search_suggest("인디", max_results=5)
        tickers = [r["ticker"] for r in out]
        self.assertIn("INDI", tickers)
        self.assertEqual(tickers[0], "INDI")

    def test_english_prefix_beats_curated_fuzzy(self):
        """'indie' → 유니버스 접두 INDI가 큐레이트 퍼지 노이즈보다 위."""
        out = sd.search_suggest("indie", max_results=5)
        self.assertEqual(out[0]["ticker"], "INDI")

    def test_exact_ticker_top(self):
        out = sd.search_suggest("INDI", max_results=5)
        self.assertEqual(out[0]["ticker"], "INDI")

    def test_curated_korean_still_works(self):
        """회귀: 큐레이트 한국어명(아마존→AMZN)은 유니버스 무관하게 유지."""
        out = sd.search_suggest("아마존", max_results=5)
        self.assertIn("AMZN", [r["ticker"] for r in out])

    def test_empty_query(self):
        self.assertEqual(sd.search_suggest("", 5), [])
        self.assertEqual(sd.search_suggest("   ", 5), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
