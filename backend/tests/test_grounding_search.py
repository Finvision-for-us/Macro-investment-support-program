"""그라운딩 검색 소스 단위테스트 — network 없음(응답 fake).

핵심 계약:
- grounding_chunks + grounding_supports → (uri, title, 청크별 snippet) 파싱.
- web_search_queries 수를 반환(무료분 소진 추적용).
- 메타데이터 없음/빈 응답은 ([], 0) — 파이프라인 무사.

실행: python backend/tests/test_grounding_search.py
"""
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.sources.grounding_search import parse_grounding


def _resp(chunks, supports, queries):
    gm = NS(grounding_chunks=chunks, grounding_supports=supports,
            web_search_queries=queries)
    return NS(candidates=[NS(grounding_metadata=gm)])


def _chunk(uri, title):
    return NS(web=NS(uri=uri, title=title))


def _support(text, indices):
    return NS(segment=NS(text=text), grounding_chunk_indices=indices)


class TestParseGrounding(unittest.TestCase):

    def test_parses_chunks_with_mapped_snippets(self):
        resp = _resp(
            chunks=[_chunk("https://r/1", "seekingalpha.com"),
                    _chunk("https://r/2", "reuters.com")],
            supports=[_support("백로그는 $7.4B이다.", [0]),
                      _support("파트너십이 확장됐다.", [0, 1])],
            queries=["q1", "q2", "q3"],
        )
        rows, n = parse_grounding(resp)
        self.assertEqual(n, 3)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "seekingalpha.com")
        self.assertIn("백로그는 $7.4B이다.", rows[0]["snippet"])
        self.assertIn("파트너십이 확장됐다.", rows[0]["snippet"])
        self.assertEqual(rows[1]["snippet"], "파트너십이 확장됐다.")

    def test_chunk_without_web_or_uri_skipped(self):
        resp = _resp(
            chunks=[NS(web=None), _chunk("", "x.com"), _chunk("https://ok", "ok.com")],
            supports=[], queries=["q"])
        rows, n = parse_grounding(resp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uri"], "https://ok")

    def test_no_metadata_safe(self):
        self.assertEqual(parse_grounding(NS(candidates=[])), ([], 0))
        self.assertEqual(parse_grounding(NS(candidates=[NS(grounding_metadata=None)])), ([], 0))
        self.assertEqual(parse_grounding(NS()), ([], 0))

    def test_snippet_capped(self):
        resp = _resp(chunks=[_chunk("https://r/1", "a.com")],
                     supports=[_support("가" * 2000, [0])], queries=[])
        rows, _ = parse_grounding(resp)
        self.assertLessEqual(len(rows[0]["snippet"]), 600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
