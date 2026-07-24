"""커버리지 산정(topic 필터) 단위테스트 — network 없음.

핵심 계약:
- topic="company"(종목 질문)면 기대 도메인은 증권 규제기관·거래소만 —
  연준/재무부/통계청 같은 거시 기관이 '미확인'으로 쌓여 커버리지가 항상
  나빠 보이던 문제(INDI 라이브 실측: 확인 2/미확인 8) 방지.
- topic="all"(거시 질문)은 기존 동작 유지(전 tier-1 기대).
- 서브도메인 매칭(www.sec.gov → sec.gov)과 searched-no-result 라벨 유지.

실행: python backend/tests/test_coverage_info.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents.jurisdiction_detector import JurisdictionResult
from app.deep_research.sources.official_source_searcher import OfficialSourceSearcher


def _searcher(searched=("sec.gov",)):
    s = OfficialSourceSearcher()
    s._last_searched_domains = set(searched)
    s._last_query_count = len(searched)
    return s


URLS = [
    "https://www.sec.gov/Archives/edgar/data/1841925/indi-20260508.htm",
    "https://investors.indie.inc/news/release-1",
]


class TestCompanyTopic(unittest.TestCase):

    def test_us_company_expects_only_securities_sources(self):
        """종목 질문 US: sec.gov만 기대 — 거시 기관은 기대치에서 제외."""
        cov = _searcher().build_coverage_info(
            JurisdictionResult(primary="US", secondary=[], is_cross_border=False),
            URLS, topic="company")
        self.assertEqual(cov["checked"], ["sec.gov"])
        self.assertEqual(cov["unchecked"], [])

    def test_cross_border_company_includes_foreign_exchanges(self):
        """US+KR 종목 질문: DART 수집 확인, 거래소·규제기관만 미확인 후보."""
        cov = _searcher().build_coverage_info(
            JurisdictionResult(primary="US", secondary=["KR"], is_cross_border=True),
            URLS + ["https://dart.fss.or.kr/report/20260101000001"], topic="company")
        self.assertIn("sec.gov", cov["checked"])
        self.assertIn("dart.fss.or.kr", cov["checked"])
        # 거시 기관(bok.or.kr 등 central_bank)은 미확인 목록에도 없어야 한다
        self.assertFalse(any("bok.or.kr" in u for u in cov["unchecked"]))
        self.assertFalse(any("federalreserve" in u for u in cov["unchecked"]))


class TestAllTopic(unittest.TestCase):

    def test_macro_keeps_full_tier1_expectation(self):
        """거시 질문(topic=all): 연준/재무부 등 전 tier-1이 기대치에 남는다."""
        cov = _searcher().build_coverage_info(
            JurisdictionResult(primary="US", secondary=[], is_cross_border=False),
            URLS, topic="all")
        unchecked_plain = [u.split(" ")[0] for u in cov["unchecked"]]
        self.assertIn("federalreserve.gov", unchecked_plain)
        self.assertIn("fred.stlouisfed.org", unchecked_plain)

    def test_searched_no_result_label(self):
        """검색은 했으나 수집 실패한 도메인은 라벨로 구분된다."""
        s = _searcher(searched=("sec.gov", "federalreserve.gov"))
        cov = s.build_coverage_info(
            JurisdictionResult(primary="US", secondary=[], is_cross_border=False),
            [], topic="all")
        self.assertIn("federalreserve.gov (searched, no result)", cov["unchecked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
