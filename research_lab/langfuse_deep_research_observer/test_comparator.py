"""comparator 재설계 단위테스트 — network/LLM 없음.

핵심 회귀: '많이 수집한 엔진이 무조건 이기는' 수량 편향이 제거됐는가.
- 물량공세(제네릭 쿼리 20개 + 저신뢰 인용 20개 + 장문 답변) vs
  품질(앵커된 쿼리 4개 + 티어1 인용 + 구조화된 답변) → 품질이 이겨야 한다.
- 자기신고 reliability_score는 채점에 사용되지 않아야 한다.
- N/A(측정 불가) 항목은 0점이 아니라 가중치 재정규화로 처리돼야 한다.

실행: python research_lab/langfuse_deep_research_observer/test_comparator.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import comparator as cp
from schema import ResearchTrace, SourceItem


def _src(url: str, reliability: float | None = None) -> SourceItem:
    return SourceItem(title=None, url=url, source_type=None, language=None,
                      reliability_score=reliability)


def _quality_trace(name: str = "quality") -> ResearchTrace:
    """소량·고품질: 앵커된 쿼리 4개(공식 1, 중문 1), 티어1/IR 인용, 구조화 답변."""
    return ResearchTrace(
        engine_name=name,
        query="INDI Wuxi 매각 의미 분석",
        generated_queries=[
            "INDI Wuxi divestiture SEC 8-K",
            "site:sec.gov INDI Wuxi",
            "indie Semiconductor Wuxi stake sale",
            "英迪 Wuxi 出售 半导体",
        ],
        official_source_queries=["site:sec.gov INDI Wuxi"],
        sources_found=[
            _src("https://www.sec.gov/Archives/edgar/data/1841925/8k.htm"),
            _src("https://www.indiesemi.com/investors/release"),
            _src("https://www.reuters.com/indi-wuxi"),
            _src("https://www.szse.cn/disclosure/wuxi"),
            _src("https://www.hkexnews.hk/wuxi-filing"),
            _src("https://www.bloomberg.com/indi"),
            _src("https://www.caixin.com/indi-wuxi"),
            _src("https://www.ft.com/indi"),
        ],
        citations=[
            _src("https://www.sec.gov/Archives/edgar/data/1841925/8k.htm"),
            _src("https://www.indiesemi.com/investors/release"),
            _src("https://www.szse.cn/disclosure/wuxi"),
        ],
        detected_jurisdictions=["US", "CN", "HK"],
        cross_source_consistency=[
            "SEC 8-K와 SZSE 공시의 매각 대가 일치",
            "회사 IR과 Reuters 보도의 지분율 일치",
            "HKEX 공시와 SEC 공시의 거래 상대방 일치",
        ],
        unverified_gaps=["CSRC 승인 일정 미확인"],
        final_answer=(
            "## 핵심 요약\n"
            "INDI는 Wuxi 지분을 매각했다 [source: https://www.sec.gov/...].\n"
            "- 매각 대가는 8-K에 공시됨 [1]\n"
            "- SZSE 공시와 교차 확인됨 [2]\n"
            "- CSRC 승인 일정은 미확인(unverified)\n"
        ),
    )


def _quantity_trace(name: str = "quantity") -> ResearchTrace:
    """물량공세: 제네릭/중복 쿼리 20개, 저신뢰 인용 20개(자기신고 0.99), 장문 답변."""
    generic = [f"semiconductor market outlook {2020 + i}" for i in range(10)]
    duplicates = ["semiconductor industry news"] * 10
    low_cites = [
        _src(f"https://seekingalpha.com/article-{i}", reliability=0.99)
        for i in range(10)
    ] + [
        _src(f"https://random-blog-{i}.io/post", reliability=0.99)
        for i in range(10)
    ]
    return ResearchTrace(
        engine_name=name,
        query="INDI Wuxi 매각 의미 분석",
        generated_queries=generic + duplicates,
        official_source_queries=[],
        sources_found=low_cites,
        citations=low_cites,
        detected_jurisdictions=["US", "CN", "KR", "JP", "HK"],  # 근거 없는 과다 주장
        cross_source_consistency=[],
        unverified_gaps=[],
        final_answer="시장 전망 서술 " * 400,  # 장문이지만 인용·구조·한계 없음
    )


class TestQuantityBiasRemoved(unittest.TestCase):

    def test_quality_beats_quantity(self):
        """[핵심 회귀] 구버전에서 물량 트레이스가 이기던 구도가 역전돼야 한다."""
        q = cp._score_trace(_quality_trace())
        m = cp._score_trace(_quantity_trace())
        self.assertGreater(q["total_normalized"], m["total_normalized"])
        # 개별 항목도: 쿼리·근거·답변 전부 품질이 우위
        self.assertGreater(q["query_generation"], m["query_generation"])
        self.assertGreater(q["evidence_quality"], m["evidence_quality"])
        self.assertGreater(q["final_answer_structure"], m["final_answer_structure"])

    def test_self_reported_reliability_ignored(self):
        """자기신고 0.99를 달아도 저신뢰 도메인 인용의 evidence_quality는 낮아야 한다."""
        m = cp._score_trace(_quantity_trace())
        # low_trust 0.15 / unknown 0.40 평균 → 0.275 근처 × 15점
        self.assertLess(m["evidence_quality"], 0.5 * cp.WEIGHTS["evidence_quality"])

    def test_long_answer_without_structure_scores_low(self):
        m = cp._score_trace(_quantity_trace())
        self.assertEqual(m["final_answer_structure"], 0.0)

    def test_overclaimed_jurisdictions_penalized(self):
        """근거(도메인/쿼리) 없는 관할 주장(KR/JP)은 자카드로 감점."""
        q = cp._score_trace(_quality_trace())
        m = cp._score_trace(_quantity_trace())
        self.assertGreater(q["jurisdiction_detection"], m["jurisdiction_detection"])


class TestCategoryMetrics(unittest.TestCase):

    def test_jurisdiction_jaccard_perfect(self):
        t = _quality_trace()
        # detected US/CN/HK 모두 sec.gov/szse.cn/hkexnews.hk 도메인으로 증거됨
        self.assertEqual(cp._jurisdiction_score(t), 1.0)

    def test_jurisdiction_none_when_no_signal(self):
        t = ResearchTrace(engine_name="x", query="q")
        self.assertIsNone(cp._jurisdiction_score(t))

    def test_query_anchoring_detects_generic(self):
        t = _quantity_trace()
        score = cp._query_generation_score(t)
        self.assertIsNotNone(score)
        self.assertLess(score, 0.3)  # 앵커 0 + 중복 다수 + 공식 0 + 단일언어

    def test_cross_validation_single_domain_halved(self):
        t = _quality_trace()
        t.citations = [_src("https://www.sec.gov/a"), _src("https://www.sec.gov/b")]
        t.sources_found = list(t.citations)
        full = cp._cross_validation_score(_quality_trace())
        halved = cp._cross_validation_score(t)
        self.assertEqual(halved, full * 0.5)

    def test_unavailable_renormalization(self):
        """인용/쿼리가 전혀 없는 트레이스: 해당 항목 N/A, 총점은 가용 가중치 기준."""
        t = ResearchTrace(
            engine_name="sparse", query="INDI",
            unverified_gaps=["gap"], final_answer="- a\n- b\n- c\nhttps://sec.gov 미확인",
        )
        s = cp._score_trace(t)
        self.assertIsNone(s["query_generation"])
        self.assertIsNone(s["evidence_quality"])
        self.assertIn("query_generation", s["unavailable"])
        # 가용 항목(cross 0 + gap 10 + answer 5)만으로 정규화 — 0이 아니어야 함
        self.assertGreater(s["total_normalized"], 0)
        self.assertLessEqual(s["total_normalized"], 100)


class TestPairwise(unittest.TestCase):

    def test_pairwise_verdicts_and_unique_domains(self):
        q, m = _quality_trace("finvision"), _quantity_trace("blogger")
        scores = {t.engine_name: cp._score_trace(t) for t in (q, m)}
        pw = cp._pairwise_comparisons([q, m], scores)
        self.assertEqual(len(pw), 1)
        pair = pw[0]
        self.assertEqual(pair["overall"]["winner"], "finvision")
        uniq = pair["unique_official_domains"]["finvision"]
        self.assertIn("sec.gov", uniq)
        self.assertEqual(pair["unique_official_domains"]["blogger"], [])
        self.assertEqual(pair["category_verdicts"]["evidence_quality"], "finvision")

    def test_citation_jaccard_zero_for_disjoint(self):
        q, m = _quality_trace("a"), _quantity_trace("b")
        scores = {t.engine_name: cp._score_trace(t) for t in (q, m)}
        self.assertEqual(cp._pairwise_comparisons([q, m], scores)[0]["citation_domain_jaccard"], 0.0)


class TestImprovements(unittest.TestCase):

    def test_finvision_low_tier_reliance_flagged(self):
        fin = _quantity_trace("finvision")
        ext = _quality_trace("gemini")
        scores = {t.engine_name: cp._score_trace(t) for t in (fin, ext)}
        items = cp._finvision_improvement_raw_material([fin, ext], scores)
        types = {i["type"] for i in items}
        self.assertIn("low_tier_citation_reliance", types)
        self.assertIn("missing_official_source", types)
        self.assertIn("gap_handling", types)

    def test_no_finvision_returns_empty(self):
        t = _quality_trace("gemini")
        self.assertEqual(cp._finvision_improvement_raw_material([t], {}), [])


class TestOfficialQueryRatio(unittest.TestCase):
    """파서가 generated/official 리스트를 독립 구축해도 비율이 1.0을 넘지 않아야 한다."""

    def test_ratio_never_exceeds_one(self):
        t = ResearchTrace(
            engine_name="x", query="INDI",
            generated_queries=["INDI Wuxi divestiture"],
            official_source_queries=[
                "site:sec.gov INDI Wuxi", "site:hkexnews.hk INDI",
                "site:csrc.gov.cn INDI",  # generated에 없는 공식 쿼리들
            ],
        )
        ratio = cp._official_query_ratio(t)
        self.assertIsNotNone(ratio)
        self.assertLessEqual(ratio, 1.0)

    def test_ratio_none_when_no_queries(self):
        self.assertIsNone(cp._official_query_ratio(ResearchTrace(engine_name="x", query="q")))

    def test_query_generation_component_bounded(self):
        """공식 쿼리가 아무리 많아도 query_generation 점수는 만점(15)을 넘지 않는다."""
        t = ResearchTrace(
            engine_name="x", query="INDI Wuxi",
            generated_queries=["INDI Wuxi"],
            official_source_queries=[f"site:sec.gov INDI q{i}" for i in range(20)],
        )
        score = cp._query_generation_score(t)
        self.assertLessEqual(score, 1.0)


class TestCompareTracesIO(unittest.TestCase):

    def test_writes_files_and_keys(self):
        with tempfile.TemporaryDirectory() as d:
            raw = cp.compare_traces([_quality_trace("finvision"), _quantity_trace("gemini")], d)
            self.assertEqual(
                set(raw.keys()),
                {"score_weights", "scores", "pairwise", "traces",
                 "finvision_improvement_raw_material"},
            )
            report = os.path.join(d, "comparison_report.md")
            self.assertTrue(os.path.exists(report))
            self.assertTrue(os.path.exists(os.path.join(d, "comparison_raw_material.json")))
            with open(report, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("## Pairwise", text)
            self.assertIn("Total(norm)", text)
            # JSON 직렬화 가능해야 함
            json.dumps(raw, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
