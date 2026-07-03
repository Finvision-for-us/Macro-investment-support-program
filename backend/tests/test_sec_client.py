"""sec_client._reduce_units_to_annual 단위 테스트 (순수 함수, network 없음).

pytest가 환경에 없으므로 표준 라이브러리 unittest로 작성.
실행: python backend/tests/test_sec_client.py

_reduce_units_to_annual: SEC companyconcept USD units → 회계연도별 연간값 축약.
  - form 10-K(+정정)만, flow는 ~1년 기간, instant는 시점값, 연도별 latest-filed 채택.
실제 SEC/network 호출은 어떤 테스트에서도 발생하지 않는다(합성 units 입력).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.sec_client import _reduce_units_to_annual


def U(start, end, val, filed, form="10-K"):
    """합성 unit 생성 헬퍼. start=None이면 시점(instant)."""
    u = {"end": end, "val": val, "filed": filed, "form": form}
    if start is not None:
        u["start"] = start
    return u


class TestReduceUnitsFlow(unittest.TestCase):
    """flow(instant=False): 기간 개념."""

    def test_comparative_years_all_kept(self):
        # 10-K 하나에 당기+전기 2개(=3년) 손익이 실린 경우 → 3개 연도 모두 유지
        units = [
            U("2021-09-26", "2022-09-24", 394328000000, "2022-10-28"),
            U("2020-09-27", "2021-09-25", 365817000000, "2022-10-28"),
            U("2019-09-29", "2020-09-26", 274515000000, "2022-10-28"),
        ]
        r = _reduce_units_to_annual(units, instant=False)
        self.assertEqual(set(r.keys()), {2020, 2021, 2022})
        self.assertEqual(r[2022]["value"], 394328000000)

    def test_quarterly_excluded(self):
        # 분기(약 90일) 기간은 연간이 아니므로 제외, 연간만 남김
        units = [
            U("2022-06-26", "2022-09-24", 90000000000, "2022-10-28"),   # 분기 ~90일 제외
            U("2021-09-26", "2022-09-24", 394328000000, "2022-10-28"),  # 연간 유지
        ]
        r = _reduce_units_to_annual(units, instant=False)
        self.assertEqual(set(r.keys()), {2022})
        self.assertEqual(r[2022]["value"], 394328000000)

    def test_restatement_latest_filed_wins(self):
        # 같은 회계연도가 두 번 보고(원본/정정) → filed 최신 값 채택
        units = [
            U("2021-09-26", "2022-09-24", 111, "2022-10-28"),           # 원본
            U("2021-09-26", "2022-09-24", 999, "2023-11-01", "10-K/A"), # 정정(최신)
        ]
        r = _reduce_units_to_annual(units, instant=False)
        self.assertEqual(r[2022]["value"], 999)

    def test_non_10k_form_excluded(self):
        units = [
            U("2021-09-26", "2022-09-24", 5, "2022-08-01", form="10-Q"),
            U("2021-09-26", "2022-09-24", 394328000000, "2022-10-28", form="10-K"),
        ]
        r = _reduce_units_to_annual(units, instant=False)
        self.assertEqual(r[2022]["value"], 394328000000)

    def test_instant_points_ignored_in_flow_mode(self):
        # flow 모드에서 start 없는 시점값은 제외
        units = [U(None, "2022-09-24", 352755000000, "2022-10-28")]
        self.assertEqual(_reduce_units_to_annual(units, instant=False), {})


class TestReduceUnitsInstant(unittest.TestCase):
    """instant(instant=True): 시점(재무상태표) 개념."""

    def test_instant_kept_flow_excluded(self):
        units = [
            U(None, "2022-09-24", 352755000000, "2022-10-28"),          # 시점값 유지
            U("2021-09-26", "2022-09-24", 394328000000, "2022-10-28"),  # 기간값 제외
        ]
        r = _reduce_units_to_annual(units, instant=True)
        self.assertEqual(set(r.keys()), {2022})
        self.assertEqual(r[2022]["value"], 352755000000)

    def test_10q_interim_balance_excluded_by_form(self):
        # 분기말 잔액(10-Q, 시점)은 form 필터로 제외 → 10-K 연도말만
        units = [
            U(None, "2022-06-25", 300000000000, "2022-08-01", form="10-Q"),
            U(None, "2022-09-24", 352755000000, "2022-10-28", form="10-K"),
        ]
        r = _reduce_units_to_annual(units, instant=True)
        self.assertEqual(set(r.keys()), {2022})
        self.assertEqual(r[2022]["value"], 352755000000)


class TestReduceUnitsGuards(unittest.TestCase):
    def test_non_list_and_bad_items(self):
        self.assertEqual(_reduce_units_to_annual(None, False), {})
        self.assertEqual(_reduce_units_to_annual("x", False), {})
        self.assertEqual(_reduce_units_to_annual([123, "y", {}], False), {})

    def test_missing_fields_skipped(self):
        units = [
            {"end": "2022-09-24", "val": 1, "form": "10-K"},                 # filed 없음
            {"start": "2021-09-26", "val": 1, "filed": "2022-10-28", "form": "10-K"},  # end 없음
            U("2021-09-26", "2022-09-24", None, "2022-10-28"),              # val None
        ]
        self.assertEqual(_reduce_units_to_annual(units, False), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
