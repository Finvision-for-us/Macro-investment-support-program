"""competitor 정제/검증 단위 테스트.

pytest가 환경에 없으므로 표준 라이브러리 unittest로 작성한다.
실행: python -m unittest backend.tests.test_stock_profile_ai
   또는: python backend/tests/test_stock_profile_ai.py

구조:
  _sanitize_competitors        : 순수 구조 정제 (네트워크 없음)
  _filter_competitors_by_valid : valid 집합으로 필터 (순수, 네트워크 없음)
  _validate_competitor_tickers : 병렬 실재성 검증 (predicate를 stub하여 네트워크 없이 검증)
실제 yfinance/Gemini/network 호출은 어떤 테스트에서도 발생하지 않는다.
"""

import asyncio
import os
import sys
import unittest

# backend 디렉터리를 import 경로에 추가 (app.services... import 위해)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.services.stock_profile_ai as spa
from app.services.stock_profile_ai import (
    _sanitize_competitors,
    _filter_competitors_by_valid,
)


_VALID = {"HPQ", "DELL", "GOOG", "SONY"}


# AAPL 후보에서 실제로 나온 결함 구조를 재현한 입력
AAPL_LIKE_INPUT = [
    {"business_area": "스마트폰", "tickers": ["SAMSUNG", "XIAOMI"],
     "descriptions": ["d-samsung", "d-xiaomi"]},
    {"business_area": "PC", "tickers": ["HPQ", "DELL"],
     "descriptions": ["d-hpq", "d-dell"]},
    {"business_area": "태블릿", "tickers": ["SAMSUNG", "LENOVO", "AAPL"],
     "descriptions": ["d-s", "d-l", "d-a"]},
    {"business_area": "웨어러블", "tickers": ["GOOG", "SONY", "GOOG"],
     "descriptions": ["d-goog", "d-sony", "d-goog-dup"]},
]


class TestSanitizeStructure(unittest.TestCase):
    """_sanitize_competitors: 구조 정제만 (SAMSUNG 등은 형식 통과로 '아직' 남는다)."""

    def test_structure_keeps_format_valid_removes_self_and_dups(self):
        out = _sanitize_competitors("AAPL", AAPL_LIKE_INPUT)
        areas = [g["business_area"] for g in out]
        # 모든 group이 형식상 살아남음 (SAMSUNG/XIAOMI/LENOVO는 형식 통과)
        self.assertEqual(areas, ["스마트폰", "PC", "태블릿", "웨어러블"])
        # 태블릿: 자기 ticker AAPL 제거, descriptions 정렬 유지
        tablet = out[2]
        self.assertEqual(tablet["tickers"], ["SAMSUNG", "LENOVO"])
        self.assertEqual(tablet["descriptions"], ["d-s", "d-l"])
        # 웨어러블: 중복 GOOG 제거, descriptions 정렬 유지
        wear = out[3]
        self.assertEqual(wear["tickers"], ["GOOG", "SONY"])
        self.assertEqual(wear["descriptions"], ["d-goog", "d-sony"])

    def test_non_list_input(self):
        self.assertEqual(_sanitize_competitors("AAPL", None), [])
        self.assertEqual(_sanitize_competitors("AAPL", {"x": 1}), [])
        self.assertEqual(_sanitize_competitors("AAPL", "string"), [])

    def test_non_dict_group_skipped(self):
        inp = ["not a dict", 123,
               {"business_area": "PC", "tickers": ["HPQ"], "descriptions": ["d"]}]
        out = _sanitize_competitors("AAPL", inp)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tickers"], ["HPQ"])

    def test_missing_business_area_skipped(self):
        inp = [{"tickers": ["HPQ"], "descriptions": ["d"]},
               {"business_area": "  ", "tickers": ["DELL"], "descriptions": ["d"]}]
        self.assertEqual(_sanitize_competitors("AAPL", inp), [])

    def test_tickers_not_list_skipped(self):
        inp = [{"business_area": "PC", "tickers": "HPQ", "descriptions": []}]
        self.assertEqual(_sanitize_competitors("AAPL", inp), [])

    def test_format_filter(self):
        # 'SAMSUNG'은 형식 통과(=구조 정제만으론 부족함을 명시), 그 외 형식 위반은 제거
        inp = [{"business_area": "x",
                "tickers": ["hpq", "HPQ", "AAPL", "123BAD", "TOOLONGTICKER", "", "SAMSUNG"],
                "descriptions": ["a", "b", "c", "d", "e", "f", "g"]}]
        out = _sanitize_competitors("AAPL", inp)
        self.assertEqual(len(out), 1)
        # hpq->HPQ, dup HPQ 제거, AAPL(self) 제거, 123BAD(숫자시작) 제거,
        # TOOLONGTICKER(>10) 제거, ""(빈값) 제거, SAMSUNG은 형식 통과로 남음
        self.assertEqual(out[0]["tickers"], ["HPQ", "SAMSUNG"])
        self.assertEqual(out[0]["descriptions"], ["a", "g"])

    def test_descriptions_shorter_than_tickers(self):
        inp = [{"business_area": "x", "tickers": ["HPQ", "DELL"], "descriptions": ["only-one"]}]
        out = _sanitize_competitors("AAPL", inp)
        self.assertEqual(out[0]["tickers"], ["HPQ", "DELL"])
        self.assertEqual(out[0]["descriptions"], ["only-one", ""])


class TestFilterByValid(unittest.TestCase):
    """_filter_competitors_by_valid: valid 집합 밖 ticker 제거 + 빈 group 제거 + 정렬 유지."""

    def test_aapl_invalid_removed_via_valid_set(self):
        structural = _sanitize_competitors("AAPL", AAPL_LIKE_INPUT)
        out = _filter_competitors_by_valid(structural, _VALID)
        areas = [g["business_area"] for g in out]
        # invalid-only group(스마트폰, 태블릿) 제거, valid group만 남음
        self.assertEqual(areas, ["PC", "웨어러블"])
        self.assertEqual(out[0]["tickers"], ["HPQ", "DELL"])
        self.assertEqual(out[0]["descriptions"], ["d-hpq", "d-dell"])
        self.assertEqual(out[1]["tickers"], ["GOOG", "SONY"])
        self.assertEqual(out[1]["descriptions"], ["d-goog", "d-sony"])
        all_t = [t for g in out for t in g["tickers"]]
        for bad in ("SAMSUNG", "XIAOMI", "LENOVO", "AAPL"):
            self.assertNotIn(bad, all_t)

    def test_alignment_preserved_when_middle_ticker_dropped(self):
        # 가운데 ticker가 invalid로 빠져도 description 정렬이 유지되는지
        groups = [{"business_area": "x", "tickers": ["HPQ", "SAMSUNG", "DELL"],
                   "descriptions": ["a", "b", "c"]}]
        out = _filter_competitors_by_valid(groups, {"HPQ", "DELL"})
        self.assertEqual(out[0]["tickers"], ["HPQ", "DELL"])
        self.assertEqual(out[0]["descriptions"], ["a", "c"])

    def test_empty_valid_set_drops_all(self):
        structural = _sanitize_competitors("AAPL", AAPL_LIKE_INPUT)
        self.assertEqual(_filter_competitors_by_valid(structural, set()), [])


class TestEndToEndPure(unittest.TestCase):
    """sanitize → filter 조합 (네트워크 없이 전체 흐름)."""

    def test_aapl_full_pure_pipeline(self):
        structural = _sanitize_competitors("AAPL", AAPL_LIKE_INPUT)
        out = _filter_competitors_by_valid(structural, _VALID)
        self.assertEqual([g["business_area"] for g in out], ["PC", "웨어러블"])


class TestValidateParallel(unittest.TestCase):
    """_validate_competitor_tickers: predicate를 stub하여 병렬 검증을 네트워크 없이 확인."""

    def test_parallel_validation_returns_valid_set(self):
        orig = spa._is_valid_competitor_ticker
        spa._is_valid_competitor_ticker = lambda s: s in _VALID  # network 대체 stub
        try:
            result = asyncio.run(
                spa._validate_competitor_tickers(["HPQ", "SAMSUNG", "DELL", "XIAOMI", "GOOG"])
            )
        finally:
            spa._is_valid_competitor_ticker = orig
        self.assertEqual(result, {"HPQ", "DELL", "GOOG"})

    def test_parallel_validation_empty_input(self):
        self.assertEqual(asyncio.run(spa._validate_competitor_tickers(set())), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
