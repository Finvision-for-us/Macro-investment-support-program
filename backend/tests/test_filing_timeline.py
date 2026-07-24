"""공시 연대기 소스 순수 함수 테스트 — network/LLM 없음.

핵심 계약:
- 최근 N년 + 대상 양식만 필터, 날짜 오름차순 (연대기 = 시간축).
- 8-K items 코드 → 한국어 사건 설명 매핑.
- 원문 추출 대상은 중요 항목(계약/인수매각/실적/채무/발행/기타사건) 8-K 최신순.
- 보도자료 첨부(EX-99 htm) 우선 선택.
- 연대기 문서에 전 공시의 날짜·설명·원문 URL 포함.

실행: python backend/tests/test_filing_timeline.py
"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.sources.filing_timeline import (
    _filter_recent, _item_desc, _pick_exhibit, _select_targets,
    _strip_html, build_chronicle,
)

_NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)

# INDI형 픽스처 — submissions API recent 병렬배열 축소본
_RECENT = {
    "form":            ["8-K", "4", "10-Q", "8-K", "8-K", "10-K", "8-K"],
    "filingDate":      ["2026-03-05", "2026-02-01", "2025-11-10",
                        "2025-11-06", "2024-12-06", "2025-02-27", "2022-01-15"],
    "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-25-000003",
                        "0001-25-000004", "0001-24-000005", "0001-25-000006",
                        "0001-22-000007"],
    "primaryDocument": ["d1.htm", "form4.xml", "q3.htm",
                        "d2.htm", "d3.htm", "tenk.htm", "old.htm"],
    "items":           ["1.01,2.03,3.02,9.01", "", "",
                        "2.02,9.01", "1.01,2.03,9.01", "", "8.01"],
}


class TestFilterRecent(unittest.TestCase):

    def test_filters_forms_years_and_sorts_ascending(self):
        out = _filter_recent(_RECENT, years=3, now=_NOW)
        # Form 4 제외, 2022(3년 밖) 제외 → 5건
        self.assertEqual(len(out), 5)
        self.assertEqual([f["form"] for f in out],
                         ["8-K", "10-K", "8-K", "10-Q", "8-K"])
        dates = [f["date"] for f in out]
        self.assertEqual(dates, sorted(dates))  # 시간순
        # accession 대시 제거
        self.assertEqual(out[0]["accession"], "000124000005")

    def test_empty_recent(self):
        self.assertEqual(_filter_recent({}, years=3, now=_NOW), [])


class TestItemDesc(unittest.TestCase):

    def test_known_codes_mapped(self):
        s = _item_desc("2.02,9.01")
        self.assertIn("실적 발표(2.02)", s)
        self.assertIn("재무제표·첨부문서(9.01)", s)

    def test_unknown_code_passthrough(self):
        self.assertEqual(_item_desc("6.66"), "6.66")

    def test_empty(self):
        self.assertEqual(_item_desc(""), "")


class TestSelectTargets(unittest.TestCase):

    def test_material_8k_only_newest_first_capped(self):
        filings = _filter_recent(_RECENT, years=3, now=_NOW)
        targets = _select_targets(filings, max_docs=2)
        # 중요 항목 8-K 3건(1.01사채×2, 2.02실적) 중 최신 2건
        self.assertEqual(len(targets), 2)
        self.assertEqual([t["date"] for t in targets],
                         ["2026-03-05", "2025-11-06"])
        # 10-K/10-Q는 원문 추출 대상 아님
        self.assertTrue(all(t["form"].startswith("8-K") for t in targets))

    def test_6k_treated_material(self):
        filings = [{"form": "6-K", "date": "2026-01-01",
                    "accession": "a", "primary_doc": "d.htm", "items": ""}]
        self.assertEqual(len(_select_targets(filings, 5)), 1)


class TestPickExhibit(unittest.TestCase):

    def test_prefers_ex99_htm(self):
        files = [{"name": "d2.htm"}, {"name": "ex99-1.htm"},
                 {"name": "ex99_2.jpg"}, {"name": "index.json"}]
        self.assertEqual(_pick_exhibit(files), "ex99-1.htm")

    def test_none_when_absent(self):
        self.assertIsNone(_pick_exhibit([{"name": "d2.htm"}]))


class TestChronicle(unittest.TestCase):

    def test_contains_dates_descriptions_urls(self):
        filings = _filter_recent(_RECENT, years=3, now=_NOW)
        text = build_chronicle("indie Semiconductor", "INDI", "1841925",
                               filings, years=3)
        self.assertIn("indie Semiconductor (INDI)", text)
        self.assertIn("총 5건", text)
        self.assertIn("2025-11-06 | 8-K", text)
        self.assertIn("실적 발표(2.02)", text)
        self.assertIn("중요 계약 체결(1.01)", text)
        self.assertIn("연차보고서", text)
        # 원문 URL — CIK는 앞자리 0 제거 정수형
        self.assertIn(
            "https://www.sec.gov/Archives/edgar/data/1841925/000125000004/d2.htm",
            text)


class TestStripHtml(unittest.TestCase):

    def test_removes_tags_scripts_and_caps(self):
        html = ("<html><script>var x=1;</script><body><h1>Q3 Results</h1>"
                "<p>Backlog of&nbsp;$7.4 billion</p></body></html>")
        text = _strip_html(html)
        self.assertIn("Q3 Results", text)
        self.assertIn("$7.4 billion", text)
        self.assertNotIn("var x", text)
        self.assertNotIn("<", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
