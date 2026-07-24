"""IR 뉴스룸 소스 순수 함수 테스트 — network/LLM 없음.

핵심 계약:
- Jina 마크다운의 날짜-링크 페어링(같은 줄/직전 줄/직후 줄) → ISO 정규화.
- 다양한 날짜 형식: 'June 10, 2026' / '10 Jun 2026' / ISO / MM/DD/YYYY.
- 컷오프(최근 N년) 밖·내비게이션 링크(짧은 제목)·소셜/이미지 링크 제외.
- 후보 스코어링: 회사 도메인 news 경로 > 통신사 애그리게이터 > 홈페이지.
- 연대기 문서에 날짜·제목·원문 URL 포함.

실행: python backend/tests/test_ir_newsroom.py
"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.sources.ir_newsroom import (
    _find_date, build_news_chronicle, parse_news_items, score_candidate,
)

_NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)

_MARKDOWN = """\
# News Releases

June 10, 2026
[indie Launches Edge AI SoC to Power Smarter Perception Systems](https://www.indiesemi.com/news/ind881)

May 14, 2026
**[indie and GlobalFoundries Expand Manufacturing Partnership Agreement](https://www.indiesemi.com/news/gf)**

[indie Semiconductor Announces Fourth Quarter 2025 Results](https://www.indiesemi.com/news/q4-2025)
Feb 27, 2026

2024-01-09 [indie Semiconductor and GEO Semiconductor Complete Merger Deal](https://www.indiesemi.com/news/geo)

01/05/2023
[Very Old Partnership Announcement Beyond The Cutoff Date](https://www.indiesemi.com/news/old)

June 10, 2026
[Contact](https://www.indiesemi.com/contact)
[Follow indie Semiconductor on social media channels](https://twitter.com/indiesemi)
"""


class TestFindDate(unittest.TestCase):

    def test_formats(self):
        self.assertEqual(_find_date("June 10, 2026 something"), "2026-06-10")
        self.assertEqual(_find_date("posted 10 Jun 2026"), "2026-06-10")
        self.assertEqual(_find_date("date: 2026-06-10"), "2026-06-10")
        self.assertEqual(_find_date("06/10/2026"), "2026-06-10")
        self.assertIsNone(_find_date("no date here"))
        self.assertIsNone(_find_date("Foobar 99, 2026"))  # 잘못된 월/일


class TestParseNewsItems(unittest.TestCase):

    def test_pairing_filtering_sorting(self):
        items = parse_news_items(_MARKDOWN, years=3, now=_NOW)
        urls = [it["url"] for it in items]
        # 컷오프 밖(2023-01-05)·짧은 제목(Contact)·소셜 링크 제외 → 4건
        self.assertEqual(len(items), 4)
        self.assertNotIn("https://www.indiesemi.com/news/old", urls)
        self.assertNotIn("https://www.indiesemi.com/contact", urls)
        self.assertTrue(all("twitter" not in u for u in urls))
        # 날짜 오름차순
        dates = [it["date"] for it in items]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], "2024-01-09")  # 같은 줄 페어링
        # 직전 줄 페어링 + 볼드 제거
        gf = next(it for it in items if it["url"].endswith("/gf"))
        self.assertEqual(gf["date"], "2026-05-14")
        self.assertFalse(gf["title"].startswith("*"))
        # 직후 줄 페어링 (제목 먼저, 날짜 다음 줄)
        q4 = next(it for it in items if it["url"].endswith("/q4-2025"))
        self.assertEqual(q4["date"], "2026-02-27")

    def test_empty(self):
        self.assertEqual(parse_news_items("", years=3, now=_NOW), [])

    def test_pdf_download_duplicate_merged_html_preferred(self):
        """Q4 플랫폼 실측 패턴: 본문 링크 + 'Download …PDF' 링크 → 1건(HTML)."""
        md = (
            "June 10, 2026\n"
            "[indie Launches Edge AI SoC to Power Perception Systems]"
            "(https://investors.indie.inc/news/ind881/default.aspx)\n"
            "[Download, indie Launches Edge AI SoC to Power Perception "
            "Systems, June 10, 2026, (opens in new window)]"
            "(https://s21.q4cdn.com/900030961/files/doc_news/ind881.pdf)\n"
        )
        items = parse_news_items(md, years=3, now=_NOW)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["url"].endswith("default.aspx"))

    def test_pdf_only_title_cleaned(self):
        """PDF 링크만 있는 항목은 'Download, ' 접두 제거 후 유지."""
        md = ("May 1, 2026\n"
              "[Download, indie Announces Something Important Today, "
              "May 1, 2026, (opens in new window)]"
              "(https://s21.q4cdn.com/900030961/files/doc_news/x.pdf)\n")
        items = parse_news_items(md, years=3, now=_NOW)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["title"].lower().startswith("download"))


class TestScoreCandidate(unittest.TestCase):

    def test_company_news_page_beats_aggregator_and_home(self):
        company, tk = "indie Semiconductor, Inc.", "INDI"
        own_news = score_candidate(
            "https://www.indiesemi.com/investor-relations/news", company, tk)
        aggregator = score_candidate(
            "https://www.prnewswire.com/news/indie-semiconductor/", company, tk)
        home = score_candidate("https://www.indiesemi.com/", company, tk)
        wiki = score_candidate(
            "https://en.wikipedia.org/wiki/Indie_Semiconductor", company, tk)
        self.assertGreater(own_news, aggregator)
        self.assertGreater(own_news, home)
        self.assertGreater(own_news, wiki)


class TestChronicle(unittest.TestCase):

    def test_contains_items_and_listing_url(self):
        items = parse_news_items(_MARKDOWN, years=3, now=_NOW)
        text = build_news_chronicle(
            "indie Semiconductor, Inc.", "INDI",
            "https://www.indiesemi.com/newsroom", items, years=3)
        self.assertIn("indie Semiconductor, Inc. (INDI)", text)
        self.assertIn("총 4건", text)
        self.assertIn("실제 수집 범위: 2024-01-09 ~ 2026-06-10", text)
        self.assertIn("2026-06-10 | indie Launches Edge AI SoC", text)
        self.assertIn("원문: https://www.indiesemi.com/news/ind881", text)
        self.assertIn("목록 페이지: https://www.indiesemi.com/newsroom", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
