"""수치 정합성 검사(결정론적) 단위테스트 — network/LLM 없음.

INDI/Wuxi 감사에서 실제로 읽은 원문(gross/net $135M, pro-rata 34.3769%)을
fixture로 재현한다. 이 검사의 목적은 LLM 산술 오류로 '허상 상충'을 만드는 것을
막고(코드가 산술을 담당), 같은 금액이 gross/net으로 갈리는 프레이밍 변화를 잡는 것.

실행: python backend/tests/test_numeric_consistency.py
      또는 pytest backend/tests/test_numeric_consistency.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents import numeric_consistency as nc
from app.deep_research.models import ExtractedContent, ResearchPlan
from app.deep_research.agents.critic import Critic


class C:
    """ExtractedContent 최소 대체(.content/.domain만 사용)."""
    def __init__(self, content, domain=""):
        self.content = content
        self.domain = domain


AUDIT_CONTENTS = [
    C("gross transaction consideration of RMB 960,834,355, or approximately "
      "$135 million, payable in cash to ADK, net of applicable local taxes.", "sec.gov"),
    C("$135 million in net cash proceeds anticipated post-closing.", "fool.com"),
    C("$135 million, payable in cash, net of applicable local taxes of "
      "roughly 10% upon closing.", "investing.com"),
    # pro-rata는 부분·전체·비율이 같은 문장(근접)에 함께 있을 때만 성립한다.
    C("乙方持有标的公司118,342,578股股份，占总股本的34.3769%，交易对价为人民币"
      "960,834,355元；对应100%股权整体作价人民币2,795,000,000元。", "sec.gov"),
    C("英迪芯微100%股权的交易作价为285,600万元。首期总对价27.95亿元。", "dfcfw.com"),
]


class TestNumericConsistency(unittest.TestCase):

    def test_pro_rata_reconciles(self):
        """부분(960,834,355) ≈ 전체(27.95亿) × 34.3769% 를 정합으로 잡는다."""
        r = nc.analyze(AUDIT_CONTENTS)
        self.assertTrue(
            any("pro-rata 정합" in s and "34.3769%" in s for s in r.consistent),
            f"pro-rata 정합 미탐지: {r.consistent}",
        )

    def test_framing_conflict_gross_net(self):
        """같은 $135M이 gross·net으로 갈리는 프레이밍 상충을 잡는다."""
        r = nc.analyze(AUDIT_CONTENTS)
        self.assertTrue(
            any("프레이밍 상충" in s and "135" in s for s in r.conflicts),
            f"gross/net 프레이밍 상충 미탐지: {r.conflicts}",
        )

    def test_tax_reconcile_mismatch_flagged(self):
        """gross×(1-10%)≠net 이면 세율 재확인을 제안한다."""
        r = nc.analyze(AUDIT_CONTENTS)
        self.assertTrue(any("세율 환산 재확인" in s for s in r.conflicts))
        self.assertTrue(any("세후" in q for q in r.followup_queries))

    def test_tax_ignores_implausible_rate_and_small_amounts(self):
        """비현실적 세율(1.2%)·소액(<100만)은 세율검사가 무시한다(실행 중 관측 잡음)."""
        r = nc.analyze([C(
            "net $500,000 and gross $500,000 with fees of 1.2%. "
            "net $160,873 gross $159,387 tax 1.2%.", "x")])
        self.assertFalse(any("세율" in s for s in r.consistent + r.conflicts))

    def test_tax_consistent_requires_colocation(self):
        """세율 '정합'은 gross·net이 같은 문장(근접)에 있을 때만. 흩어진 서로 다른
        총액이 세율로 우연히 맞아도 정합으로 잡지 않는다(실행 중 관측: $135M↔$150M).
        단, 같은 수치를 gross/net으로 혼용하는 '상충'은 교차출처로도 유지한다."""
        # 흩어진 다른 총액 → 정합 아님
        scattered = nc.analyze([
            C("net cash proceeds of $135 million from the divestiture.", "fool"),
            C("aggregate gross proceeds of $150 million from notes, fees 9.98%.", "notes")])
        self.assertFalse(any("세율 정합" in s for s in scattered.consistent))
        # 같은 문장 gross+net+세율 → 정합
        co = nc.analyze([C(
            "gross proceeds of $135 million, net proceeds of $121.5 million after 10% tax.", "s")])
        self.assertTrue(any("세율 정합" in s for s in co.consistent))
        # 교차출처 같은 금액 gross/net 혼용 → 상충 유지
        cf = nc.analyze([
            C("gross proceeds of $135 million, net of applicable taxes of 10%.", "sec"),
            C("$135 million in net cash proceeds.", "fool")])
        self.assertTrue(any("세율 환산 재확인" in s for s in cf.conflicts))

    def test_framing_suppresses_small_and_fewsource(self):
        """소액(<100만) 또는 소수 출처(<3)의 gross/net 우연 근접은 억제한다.
        (실행 중 관측: $600k·$5M 등 line item 과다 발화.)"""
        r = nc.analyze([
            C("gross $600,210 fee. net $600,210 cost.", "a"),
            C("gross $600,210 again. net $600,210 again.", "b"),
            C("gross $5 million margin. net $5 million income.", "c"),
            C("gross $5 million. net $5 million.", "d")])
        self.assertFalse(any("프레이밍" in s for s in r.conflicts))

    def test_framing_keeps_prominent_deal_amount(self):
        """딜 핵심 금액이 3+ 출처에서 gross/net으로 갈리면 유지."""
        r = nc.analyze([
            C("gross proceeds of $135 million, net of applicable taxes.", "sec"),
            C("$135 million in net cash proceeds.", "fool"),
            C("$135 million net, gross basis noted elsewhere.", "inv")])
        self.assertTrue(any("프레이밍" in s and "135" in s for s in r.conflicts))

    def test_prorata_rejects_near_100pct_ratio(self):
        """근-100% 비율(99.26%)은 두 거의같은 큰 숫자를 우연히 맞추므로 배제."""
        r = nc.analyze([C(
            "标的公司持股99.26%股权，账面2,949,180.57万元，整体作价2,988,878.57万元。", "x")])
        self.assertFalse(any("pro-rata" in s for s in r.consistent))

    def test_clean_data_no_false_conflict(self):
        """상충 없는 데이터에서 오탐이 없어야 한다."""
        clean = [
            C("Cash and cash equivalents $174,433 thousand as of March 31, 2026.", "sec.gov"),
            C("Revenue was $62 million at the midpoint for Q2 2026.", "fool.com"),
        ]
        r = nc.analyze(clean)
        self.assertEqual(r.conflicts, [])

    def test_empty_input(self):
        r = nc.analyze([])
        self.assertEqual(r.conflicts, [])
        self.assertEqual(r.consistent, [])
        self.assertEqual(r.followup_queries, [])

    def test_prorata_no_false_positive_on_noise(self):
        """숫자 많은 코퍼스에서 같은 비율로 우연히 맞는 무관한 쌍을 pro-rata로
        오탐하지 않는다(전체는 100%/총액 문맥 필수). 실제 실행에서 관측된 버그."""
        noise = [
            C("持有 34.38% 股权。营收 7.2 亿。市盈率 21 倍。毛利率 10%。", "x"),
            C("研发人员 4,001 万元 지출。总资产 11,592.46万元。응수 440.75 万元 및 1300万元.", "y"),
        ]
        r = nc.analyze(noise)
        prorata = [s for s in r.consistent if "pro-rata" in s]
        self.assertEqual(prorata, [], f"pro-rata 오탐: {prorata}")

    def test_nonequity_percent_not_used_for_prorata(self):
        """지분이 아닌 일반 비율(占营收 10%)은 pro-rata에 쓰이지 않는다."""
        r = nc.analyze([C(
            "研发投入 4,001 万元，占营收 10%，营业收入总额 40,001 万元。", "x")])
        self.assertFalse(any("pro-rata" in s for s in r.consistent))

    def test_prorata_colocated_and_crossource(self):
        """같은 문장 근접이면 '[pro-rata 정합]'. 흩어져 있어도 지분블록↔100%총액이
        거의 정확히 맞으면 '[pro-rata 정합·교차출처]'로 인정(엔티티 연결)."""
        co = nc.analyze([
            C("占总股本的34.3769%，交易对价人民币960,834,355元；对应100%股权整体作价"
              "人民币2,795,000,000元。", "a")])
        self.assertTrue(any("[pro-rata 정합]" in s for s in co.consistent))
        scattered = nc.analyze([
            C("占总股本的34.3769%。交易对价人民币960,834,355元。", "a"),
            C("100%股权整体作价人民币2,795,000,000元。", "b")])
        # 흩어진 경우: plain co-located 태그는 없고, 교차출처 태그로 인정된다.
        self.assertFalse(any("[pro-rata 정합]" in s and "교차출처" not in s
                             for s in scattered.consistent))
        self.assertTrue(any("교차출처" in s for s in scattered.consistent))

    def test_same_amount_deduped(self):
        """같은 금액의 영/중 표기가 (co-located) pro-rata 정합에서 중복 출력되지 않는다."""
        r = nc.analyze(AUDIT_CONTENTS)
        prorata = [s for s in r.consistent if "[pro-rata 정합]" in s]
        self.assertEqual(len(prorata), 1, f"pro-rata 정합 중복: {prorata}")


class TestCrossSourceProrata(unittest.TestCase):

    def test_audit_case_fires_via_exact_match(self):
        """감사 실제 케이스(标的公司 vs 英迪芯微 코어퍼런스, 앵커 공유 없음)도
        거의 정확(≤0.3%) 일치면 교차출처 정합으로 인정."""
        r = nc.analyze([
            C("占标的公司总股本的34.3769%，交易对价为人民币960,834,355元。", "sec"),
            C("英迪芯微100%股权的交易作价为285,600万元。首期总对价27.95亿元。", "dfcfw")])
        self.assertTrue(any("교차출처" in s and "34.3769%" in s for s in r.consistent))

    def test_fires_via_shared_anchor(self):
        """공유 앵커(종목코드 301112)가 있으면 엔티티 연결로 교차출처 정합."""
        r = nc.analyze([
            C("United Faith(301112) acquired a 40% equity stake for $80,000,000.", "x"),
            C("The 301112 deal values the target at aggregate 100% consideration of $200,000,000.", "y")])
        self.assertTrue(any("교차출처" in s and "301112" in s for s in r.consistent))

    def test_unrelated_scattered_stays_silent(self):
        """앵커 공유도 없고 거의 정확도 아닌 무관한 흩어진 쌍은 침묵한다."""
        r = nc.analyze([
            C("segment revenue $52,000,000 with a 34.38% margin.", "a"),
            C("aggregate gross proceeds of $150,000,000 from convertible notes.", "b")])
        self.assertFalse(any("교차출처" in s for s in r.consistent))

    def test_round_ratio_without_anchor_silent(self):
        """라운드 비율(10%)은 라운드 금액과 우연히 정확히 맞아도, 앵커가 없으면
        교차출처 정합으로 인정하지 않는다(실행 중 관측: 4,001만↔4亿×10%)."""
        r = nc.analyze([
            C("研发投入 4,001 万元，持有 10% 股权。", "x"),
            C("总资产整体作价 4 亿元。", "y")])
        self.assertFalse(any("교차출처" in s for s in r.consistent))

    def test_round_ratio_with_shared_anchor_ok(self):
        """라운드 비율이라도 공유 앵커가 있으면 엔티티 연결로 인정."""
        r = nc.analyze([
            C("United Faith(301112) acquired a 40% equity stake for $80,000,000.", "x"),
            C("The 301112 deal aggregate 100% consideration of $200,000,000.", "y")])
        self.assertTrue(any("교차출처" in s and "301112" in s for s in r.consistent))

    def test_ambiguous_multiple_wholes_silent(self):
        """전체 후보가 2개 이상(모호)이면 침묵한다."""
        r = nc.analyze([
            C("占34.3769%股权，交易对价人民币960,834,355元。", "a"),
            C("100%整体作价人民币2,795,000,000元。", "b"),
            C("另一估算100%总额人民币2,796,000,000元。", "c")])
        self.assertFalse(any("교차출처" in s for s in r.consistent))


class TestKoreanMoneyParsing(unittest.TestCase):
    """한국어 금액 표기(조/억/만) 파싱 — FinVision 리포트는 한국어라 이 표기가
    지배적이며, 미지원 시 XBRL 원장 대조가 무력화된다(E2E에서 실측 확정)."""

    def _usd(self, text):
        return [(m.currency, round(m.value)) for m in nc.extract_mentions(text)
                if m.kind == "money"]

    def test_eok_man_combo(self):
        self.assertEqual(self._usd("약 1억 3,500만 달러"), [("USD", 135_000_000)])

    def test_man_only(self):
        self.assertEqual(self._usd("2,500만 달러 규모"), [("USD", 25_000_000)])

    def test_jo_eok_combo(self):
        """조 미지원이면 '5,000억'만 잡혀 틀린 부분값이 유입된다 — 반드시 3.5e12."""
        self.assertEqual(self._usd("시가총액 3조 5,000억 원"),
                         [("KRW", 3_500_000_000_000)])

    def test_korean_currencies(self):
        self.assertEqual(self._usd("총 27억 9,500만 위안"), [("RMB", 2_795_000_000)])

    def test_mixed_korean_and_symbol(self):
        got = dict(self._usd("약 1억 3,500만 달러(RMB 960,834,355)에 매각"))
        self.assertEqual(got.get("USD"), 135_000_000)
        self.assertEqual(got.get("RMB"), 960_834_355)


class TestFableReviewedBugs(unittest.TestCase):
    """Fable 5 리뷰로 확정된 버그 회귀 방지."""

    def _monies(self, text):
        return [(m.currency, round(m.value)) for m in nc.extract_mentions(text)
                if m.kind == "money"]

    def test_bug1_cjk_unit_currency(self):
        """亿美元→USD, 人民币X亿元→RMB (단위·통화 오분류 금지)."""
        got = dict(self._monies("作价1.35亿美元(约合人民币9.6亿元)"))
        self.assertEqual(got.get("USD"), 135_000_000)
        self.assertEqual(got.get("RMB"), 960_000_000)

    def test_bug2_hk_dollar_and_symbolless_usd(self):
        """HK$→HKD(USD 아님), 기호 없는 USD도 추출."""
        self.assertIn(("HKD", 1_050_000_000), self._monies("HK$1,050 million"))
        self.assertIn(("USD", 135_000_000), self._monies("a total of USD 135 million"))

    def test_bare_cjk_yuan(self):
        """접두 없는 960,834,355元도 RMB로 추출."""
        self.assertIn(("RMB", 960_834_355), self._monies("交易对价960,834,355元。"))

    def test_no_currency_dup(self):
        """人民币960,834,355元이 중복 mention으로 추출되지 않는다."""
        self.assertEqual(len(self._monies("人民币960,834,355元")), 1)

    def test_bug4_crossource_dedup_same_value(self):
        """전체금액이 여러 출처에 '같은 값'으로 인용되면(모호 아님) 교차출처 정합 발화."""
        r = nc.analyze([
            C("占标的公司总股本的34.3769%，交易对价为960,834,355元。", "sec"),
            C("英迪芯微整体作价27.95亿元。", "dfcfw"),
            C("独立财务顾问确认整体作价27.95亿元。", "gtja")])
        self.assertTrue(any("교차출처" in s for s in r.consistent))

    def test_bug4_crossource_ambiguous_different_values(self):
        """서로 다른 전체값이 2개면 여전히 모호로 침묵."""
        r = nc.analyze([
            C("占34.3769%，交易对价960,834,355元。", "sec"),
            C("整体作价27.95亿元。", "a"), C("整体作价30.00亿元。", "b")])
        self.assertFalse(any("교차출처" in s for s in r.consistent))

    def test_bug5_cross_checker_comma_numbers(self):
        """cross_checker가 콤마 숫자를 파편화하지 않고 값으로 비교."""
        from app.deep_research.agents.cross_checker import (
            _numeric_values, _find_contradicting_numbers)
        self.assertEqual(_numeric_values("RMB 960,834,355"), [960834355.0])
        self.assertEqual(
            _find_contradicting_numbers("revenue 960,834,355", "revenue: 960,834,355"), [])
        self.assertTrue(
            _find_contradicting_numbers("revenue 960,834,355", "revenue: 150,000,000"))


class TestFableDesignObservations(unittest.TestCase):
    """Fable 5 설계 관찰 반영 회귀."""

    def test_anchor_boilerplate_not_linked(self):
        """흔한 영어 boilerplate(Company/Shares 등)는 앵커가 되지 않아 거짓 연결 안 함."""
        self.assertEqual(nc._extract_anchors("The Company sold Shares pursuant Agreement"), frozenset())
        r = nc.analyze([
            C("The Company holds a 40% equity stake acquired for $80,000,000.", "x"),
            C("The Company reported aggregate 100% consideration of $200,000,000.", "y")])
        self.assertFalse(any("교차출처" in s for s in r.consistent))

    def test_real_proper_noun_still_anchor(self):
        anchors = nc._extract_anchors("United Faith Wuxi Microelectronics 301112")
        self.assertIn("301112", anchors)
        self.assertIn("Wuxi", anchors)

    def test_prorata_shows_actual_error(self):
        r = nc.analyze([C(
            "占总股本的34.3769%，交易对价960,834,355元；对应100%整体作价2,795,000,000元。", "a")])
        line = next(s for s in r.consistent if "pro-rata 정합]" in s)
        self.assertRegex(line, r"오차 \d+\.\d+%")
        self.assertNotIn("≤", line)

    def test_syndication_dedup(self):
        """동일 기사가 여러 도메인에 전재되면 1출처로 계수 → 프레이밍 과다발화 방지."""
        art = ("indie will receive gross proceeds of $135 million from the Wuxi "
               "divestiture, net of applicable local taxes payable to ADK at closing.")
        r = nc.analyze([C(art, "reuters"), C(art, "yahoo"), C(art, "msn")])
        self.assertFalse(any("프레이밍" in s for s in r.conflicts))
        # 서로 다른 3기사는 유지
        r2 = nc.analyze([
            C("gross proceeds of $135 million per the SEC filing.", "sec"),
            C("$135 million in net cash proceeds on the call.", "fool"),
            C("the $135 million net figure differs from gross.", "inv")])
        self.assertTrue(any("프레이밍" in s for s in r2.conflicts))


class TestCriticIntegration(unittest.TestCase):

    def test_fallback_appends_numeric_gaps(self):
        """LLM 없이도(_fallback) 결정론적 수치검사가 gaps·쿼리에 병합된다."""
        contents = [
            ExtractedContent(url=f"u{i}", title=c.domain, content=c.content, domain=c.domain)
            for i, c in enumerate(AUDIT_CONTENTS)
        ]
        plan = ResearchPlan(original_query="indie Wuxi 매각", sub_queries=[],
                            required_sections=["사건팩트"])
        res = Critic()._fallback_analysis(plan, contents)
        self.assertTrue(any("프레이밍 상충" in g for g in res.gaps))
        self.assertTrue(any("세후" in q.query for q in res.additional_queries))
        self.assertIn("수치정합", res.reasoning)


class TestCrossCurrency(unittest.TestCase):

    def test_normal_fx_reconciles(self):
        """RMB 960,834,355 ≈ $135M → 함축 FX ~7.1 정합."""
        r = nc.analyze([C(
            "gross transaction consideration of RMB 960,834,355, or "
            "approximately $135 million, payable in cash.", "sec.gov")])
        self.assertTrue(any("환율 정합" in s and "7.1" in s for s in r.consistent))
        self.assertEqual(r.conflicts, [])

    def test_unit_error_flagged(self):
        """RMB를 10배로 오표기하면 함축 FX가 밴드 밖 → 환율 이상."""
        r = nc.analyze([C(
            "consideration of RMB 9,608,343,550, or approximately "
            "$135 million in cash.", "x")])
        self.assertTrue(any("환율 이상" in s for s in r.conflicts))
        self.assertTrue(any("환율" in q for q in r.followup_queries))

    def test_small_number_not_paired_as_fx(self):
        """작은 RMB 숫자가 큰 USD 금액과 근접해도 환율쌍으로 오탐하지 않는다.
        (실제 실행에서 관측: 함축FX≈0.00 잡음)"""
        r = nc.analyze([C(
            "배당 RMB 328.0 만 대비 매출 $50.5 million, 부채 RMB 7.2 대 $1.2 million.", "y")])
        self.assertFalse(any("환율" in s for s in r.conflicts + r.consistent))

    def test_no_pair_no_flag(self):
        """단일 통화만 있으면 환율 검사가 아무것도 만들지 않는다."""
        r = nc.analyze([C("交易对价为人民币960,834,355元。", "sec.gov")])
        self.assertFalse(any("환율" in s for s in r.consistent + r.conflicts))


class TestSynthesizerExposure(unittest.TestCase):

    def test_numeric_lines_exposed_to_cross_validation(self):
        """Synthesizer._numeric_cross_validation이 정합/상충 문장을 반환한다."""
        from app.deep_research.agents.synthesizer import Synthesizer
        contents = [
            ExtractedContent(url=f"u{i}", title=c.domain, content=c.content, domain=c.domain)
            for i, c in enumerate(AUDIT_CONTENTS)
        ]
        try:
            syn = Synthesizer()
        except Exception:
            syn = Synthesizer.__new__(Synthesizer)
        cv = syn._numeric_cross_validation(contents)
        self.assertTrue(any("pro-rata 정합" in s for s in cv))
        self.assertTrue(any("프레이밍 상충" in s for s in cv))
        self.assertLessEqual(len(cv), 8)
        self.assertEqual(syn._numeric_cross_validation([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
