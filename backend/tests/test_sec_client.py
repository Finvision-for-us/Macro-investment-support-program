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

from app.services.sec_client import (
    _reduce_units_to_annual,
    SEC_CONCEPT_MAP,
    merge_annual_by_fy,
    split_adjust_by_filed,
)


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


class TestConceptMapWellFormed(unittest.TestCase):
    """SEC_CONCEPT_MAP 구조 검증 (오타/누락 방지, network 없음)."""

    ALLOWED_UNITS = {"USD", "shares", "USD/shares"}

    def test_each_spec_valid(self):
        self.assertTrue(SEC_CONCEPT_MAP)
        for key, spec in SEC_CONCEPT_MAP.items():
            self.assertIsInstance(spec.get("concepts"), list, key)
            self.assertTrue(spec["concepts"], key)  # 비어있지 않음
            self.assertTrue(all(isinstance(c, str) and c for c in spec["concepts"]), key)
            self.assertIsInstance(spec.get("instant"), bool, key)
            self.assertIn(spec.get("unit"), self.ALLOWED_UNITS, key)
            if "negate" in spec:
                self.assertIsInstance(spec["negate"], bool, key)


class TestMergeAnnualByFy(unittest.TestCase):
    """merge_annual_by_fy: SEC(장기) ⊕ Yahoo(최근) 회계연도 병합 (순수 함수)."""

    def _sec(self, *pairs):
        return [{"fy": int(e[:4]), "end": e, "value": v} for e, v in pairs]

    def _yahoo(self, *pairs):
        return [{"date": d, "value": v} for d, v in pairs]

    def test_sec_extends_history_and_overrides_overlap(self):
        sec = self._sec(("2020-09-26", 274), ("2021-09-25", 365), ("2022-09-24", 394))
        yahoo = self._yahoo(("2021-09-30", 365), ("2022-09-30", 394))  # 겹침
        out = merge_annual_by_fy(sec, yahoo)
        # 3개 연도, 겹치는 해는 SEC 날짜(end)로 통일, 오래된 2020은 SEC에서만
        self.assertEqual([p["date"] for p in out],
                         ["2020-09-26", "2021-09-25", "2022-09-24"])
        self.assertEqual([p["value"] for p in out], [274, 365, 394])

    def test_yahoo_fills_year_sec_lacks(self):
        # SEC엔 없고 Yahoo에만 있는 최신 회계연도(예: 10-K 미제출)는 Yahoo로 채움
        sec = self._sec(("2024-09-28", 391))
        yahoo = self._yahoo(("2024-09-30", 391), ("2025-09-30", 416))
        out = merge_annual_by_fy(sec, yahoo)
        self.assertEqual([p["date"] for p in out], ["2024-09-28", "2025-09-30"])
        self.assertEqual(out[-1]["value"], 416)  # 2025는 Yahoo

    def test_empty_sec_returns_yahoo(self):
        yahoo = self._yahoo(("2024-09-30", 391), ("2025-09-30", 416))
        out = merge_annual_by_fy([], yahoo)
        self.assertEqual([p["value"] for p in out], [391, 416])

    def test_both_empty(self):
        self.assertEqual(merge_annual_by_fy([], []), [])
        self.assertEqual(merge_annual_by_fy(None, None), [])

    def test_bad_points_skipped(self):
        sec = [{"end": None, "value": 1}, {"end": "2020-09-26", "value": None}, 123]
        yahoo = [{"date": "2019-09-28", "value": 200}, "x"]
        out = merge_annual_by_fy(sec, yahoo)
        self.assertEqual(out, [{"date": "2019-09-28", "value": 200}])


class TestSplitAdjustByFiled(unittest.TestCase):
    """split_adjust_by_filed: filed일 이후 분할로만 조정 (순수 함수)."""

    def _raw(self, *tuples):  # (fy, end, value, filed)
        return [{"fy": fy, "end": e, "value": v, "filed": f} for fy, e, v, f in tuples]

    def test_adjust_only_by_splits_after_filed(self):
        # 2020년 4:1 분할. FY2019 값은 분할 前 filed → ÷4. FY2021 값은 분할 後 filed → 그대로.
        raw = self._raw(
            (2019, "2019-09-28", 12.0, "2019-10-30"),   # 분할 전 보고
            (2021, "2021-09-25", 5.61, "2021-10-29"),   # 분할 후 보고(이미 조정됨)
        )
        splits = {"2020-08-31": 4.0}
        out = {p["fy"]: p["value"] for p in split_adjust_by_filed(raw, splits)}
        self.assertEqual(out[2019], 3.0)     # 12.0 / 4
        self.assertEqual(out[2021], 5.61)    # 그대로

    def test_multiple_splits_compound(self):
        # 2014년 7:1 + 2020년 4:1. 둘 다 이후 filed면 ÷28.
        raw = self._raw((2013, "2013-09-28", 39.2, "2013-10-30"))
        splits = {"2014-06-09": 7.0, "2020-08-31": 4.0}
        out = split_adjust_by_filed(raw, splits)
        self.assertEqual(out[0]["value"], round(39.2 / 28, 4))

    def test_no_splits_returns_values(self):
        raw = self._raw((2024, "2024-09-28", 6.08, "2024-11-01"))
        self.assertEqual(split_adjust_by_filed(raw, {})[0]["value"], 6.08)

    def test_guards(self):
        self.assertEqual(split_adjust_by_filed(None, {}), [])
        self.assertEqual(split_adjust_by_filed([{"value": 1}], {}), [])  # end 없음
        self.assertEqual(split_adjust_by_filed([{"end": "2020-01-01", "value": None, "filed": "x"}], {}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
