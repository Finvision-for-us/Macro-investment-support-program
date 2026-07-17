"""cninfo 공시 소스 단위테스트 — network/akshare 없음(fetcher fake 주입).

핵심 계약:
- A주 코드가 쿼리/컨텍스트에 없으면 조회 자체를 안 한다(보수적 — 무할루시네이션).
- 연월(202606 등) 오탐 방지: '19'/'20' 시작 6자리는 코드로 취급하지 않는다.
- 공시 링크는 정적 PDF URL(finalpage/{날짜}/{ID}.PDF)로 변환(라이브 실측 패턴).
- akshare 불가/조회 실패는 빈 결과·해당 코드 스킵 — 파이프라인 무사.

실행: python backend/tests/test_cninfo_disclosure.py
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.sources import cninfo_disclosure as cd


def _run(coro):
    return asyncio.run(coro)


DETAIL = ("http://www.cninfo.com.cn/new/disclosure/detail?stockCode=301112"
          "&announcementId=1225387929&orgId=x&announcementTime=2026-06-26")

ROWS = [
    {"title": "关于出售参股公司股权的公告", "time": "2026-06-26 00:00:00", "link": DETAIL},
    {"title": "2025年年度报告", "time": "2026-04-20 00:00:00",
     "link": DETAIL.replace("1225387929", "1225000001").replace("2026-06-26", "2026-04-20")},
    {"title": "监事会决议公告", "time": "2026-03-01 00:00:00",
     "link": DETAIL.replace("1225387929", "1224999999").replace("2026-06-26", "2026-03-01")},
]


class _Patched(unittest.TestCase):
    """akshare 가용성·fetcher를 케이스별로 주입."""

    def setUp(self):
        self._orig = (cd._akshare_checked, cd._akshare_ok, cd._fetch_disclosures_sync)
        cd._akshare_checked, cd._akshare_ok = True, True

    def tearDown(self):
        cd._akshare_checked, cd._akshare_ok, cd._fetch_disclosures_sync = self._orig

    def _set_fetcher(self, fn):
        cd._fetch_disclosures_sync = fn


class TestCodeExtraction(unittest.TestCase):

    def test_valid_prefixes(self):
        text = "심천 000001, 창업판 301112, 상해 600519, 과창판 688981, 북증 830001"
        self.assertEqual(
            cd.extract_a_share_codes(text),
            ["000001", "301112", "600519", "688981", "830001"])

    def test_date_like_not_matched(self):
        """연월 202606·연도확장 199001은 코드가 아니다(오탐 방지)."""
        self.assertEqual(cd.extract_a_share_codes("2026년 6월(202606) 보고서 199001"), [])

    def test_dedup_keeps_order(self):
        self.assertEqual(cd.extract_a_share_codes("301112 유관 301112 그리고 000001"),
                         ["301112", "000001"])


class TestPdfUrl(unittest.TestCase):

    def test_parses_detail_link(self):
        self.assertEqual(
            cd._pdf_url_from_link(DETAIL, ""),
            "http://static.cninfo.com.cn/finalpage/2026-06-26/1225387929.PDF")

    def test_falls_back_to_time_str(self):
        link = "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=123456"
        self.assertEqual(
            cd._pdf_url_from_link(link, "2026-06-26 00:00:00"),
            "http://static.cninfo.com.cn/finalpage/2026-06-26/123456.PDF")

    def test_invalid_returns_none(self):
        self.assertIsNone(cd._pdf_url_from_link("http://x.com/detail?foo=1", ""))
        self.assertIsNone(cd._pdf_url_from_link("", ""))


class TestRanking(unittest.TestCase):

    def test_keyword_match_ranks_first(self):
        picked = cd._rank_rows(ROWS, "股权 出售 관련 공시")
        self.assertEqual(picked[0]["title"], "关于出售参股公司股权的公告")

    def test_no_match_falls_back_to_recent(self):
        picked = cd._rank_rows(ROWS, "totally unrelated query")
        self.assertEqual(picked[0]["time"], "2026-06-26 00:00:00")  # 최신순
        self.assertLessEqual(len(picked), cd._RECENT_FALLBACK)


class TestSearchDisclosures(_Patched):

    def test_no_code_no_fetch(self):
        called = []
        self._set_fetcher(lambda *a: called.append(a) or [])
        out = _run(cd.cninfo_disclosure_source.search_disclosures(
            "indie Semiconductor Wuxi 매각 분석"))
        self.assertEqual(out, [])
        self.assertEqual(called, [])

    def test_code_in_query_fetches_and_builds_pdf_results(self):
        self._set_fetcher(lambda code, s, e: list(ROWS))
        out = _run(cd.cninfo_disclosure_source.search_disclosures(
            "301112 出售 股权 관련 공시 확인"))
        self.assertTrue(out)
        top = out[0]
        self.assertTrue(top.url.startswith("http://static.cninfo.com.cn/finalpage/"))
        self.assertTrue(top.url.endswith(".PDF"))
        self.assertEqual(top.source_type, "official")
        self.assertIn("301112", top.title)
        self.assertEqual(top.published_date, "2026-06-26")

    def test_context_cn_ticker_used(self):
        fetched = []
        self._set_fetcher(lambda code, s, e: fetched.append(code) or list(ROWS))
        out = _run(cd.cninfo_disclosure_source.search_disclosures(
            "무석 자회사 공시", context={"cn_ticker": "301112"}))
        self.assertEqual(fetched, ["301112"])
        self.assertTrue(out)

    def test_fetch_failure_skips_code(self):
        def explode(code, s, e):
            raise RuntimeError("cninfo down")
        self._set_fetcher(explode)
        out = _run(cd.cninfo_disclosure_source.search_disclosures("301112 공시"))
        self.assertEqual(out, [])

    def test_akshare_unavailable_empty(self):
        cd._akshare_ok = False
        out = _run(cd.cninfo_disclosure_source.search_disclosures("301112 공시"))
        self.assertEqual(out, [])

    def test_max_codes_cap(self):
        fetched = []
        self._set_fetcher(lambda code, s, e: fetched.append(code) or [])
        _run(cd.cninfo_disclosure_source.search_disclosures(
            "000001 301112 600519 688981 전부 확인"))
        self.assertEqual(len(fetched), cd._MAX_CODES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
