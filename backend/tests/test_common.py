"""deep_research 공유 유틸(common.py) 단위테스트 — network/LLM 없음.

domain_of: ~13곳에 흩어져 있던 도메인 추출을 단일화(+대소문자 정규화).
parse_json_object: planner/critic/synthesizer의 동일 복제 _parse_json 단일 소스.

실행: python backend/tests/test_common.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.common import domain_of, parse_json_object


class TestDomainOf(unittest.TestCase):

    def test_strips_www_and_scheme(self):
        self.assertEqual(domain_of("https://www.sec.gov/Archives/x"), "sec.gov")
        self.assertEqual(domain_of("http://reuters.com/article"), "reuters.com")

    def test_lowercases(self):
        """대소문자 정규화 — 이전 일부 호출부는 소문자화를 안 해 매칭이 어긋났다."""
        self.assertEqual(domain_of("https://WWW.WSJ.COM/x"), "wsj.com")
        self.assertEqual(domain_of("https://Bloomberg.Com"), "bloomberg.com")

    def test_none_and_empty_safe(self):
        self.assertEqual(domain_of(None), "")
        self.assertEqual(domain_of(""), "")

    def test_subdomain_preserved(self):
        self.assertEqual(domain_of("https://ir.apple.com/news"), "ir.apple.com")

    def test_port_and_path(self):
        self.assertEqual(domain_of("http://example.com:8080/a/b?q=1"), "example.com:8080")


class TestParseJsonObject(unittest.TestCase):

    def test_plain_json(self):
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})

    def test_code_fence_stripped(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_object('```\n{"b": 2}\n```'), {"b": 2})

    def test_embedded_object_extracted(self):
        text = 'Here is the result:\n{"x": 10, "y": 20}\nThanks!'
        self.assertEqual(parse_json_object(text), {"x": 10, "y": 20})

    def test_empty_and_none(self):
        self.assertIsNone(parse_json_object(""))
        self.assertIsNone(parse_json_object("no json here"))

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_json_object("{not valid json"))

    def test_nested_object(self):
        self.assertEqual(
            parse_json_object('{"a": {"b": [1, 2]}}'), {"a": {"b": [1, 2]}}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
