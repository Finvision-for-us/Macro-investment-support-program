"""구조화 출력 이식(google-genai response_schema) 단위테스트 — network/LLM 없음.

검증 대상:
- llm_client._validate / _output_tokens / _is_quota_error
- planner/critic/synthesizer/lead_follower의 '구조화 1차 → 레거시 2차' 폴백 계약
- _self_verify의 빈 껍데기 방어(검증 패스가 보고서를 지우면 안 됨)

실행: python backend/tests/test_structured_output.py
"""

import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research import llm_client
from app.deep_research.agents import planner as planner_mod
from app.deep_research.agents import critic as critic_mod
from app.deep_research.agents import synthesizer as synth_mod
from app.deep_research.discovery import lead_follower as lead_mod
from app.deep_research.models import ExtractedContent, ResearchPlan, SubQuery

StructuredResult = llm_client.StructuredResult


def _run(coro):
    return asyncio.run(coro)


def _fake_resp(text: str, parsed=None, out_tokens=None):
    um = SimpleNamespace(candidates_token_count=out_tokens) if out_tokens else None
    return SimpleNamespace(text=text, parsed=parsed, usage_metadata=um)


class _FakeLegacyModel:
    """레거시 google.generativeai 모델 흉내 — generate_content가 .text를 반환."""

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    def generate_content(self, prompt, request_options=None):
        self.calls += 1
        return SimpleNamespace(text=self._text)


class _Patched(unittest.TestCase):
    """llm_client.generate_structured를 케이스별로 갈아끼우는 공통 베이스."""

    def setUp(self):
        self._orig = llm_client.generate_structured

    def tearDown(self):
        llm_client.generate_structured = self._orig

    def _set_structured(self, result):
        async def fake(prompt, schema, model, **kw):
            return result
        llm_client.generate_structured = fake


class TestLlmClientHelpers(unittest.TestCase):

    def test_quota_error_detection(self):
        self.assertTrue(llm_client._is_quota_error(Exception("429 RESOURCE_EXHAUSTED")))
        self.assertTrue(llm_client._is_quota_error(Exception("Quota exceeded")))
        self.assertFalse(llm_client._is_quota_error(Exception("timeout")))

    def test_validate_basemodel_from_parsed(self):
        m = planner_mod.PlanOut(language="en")
        out = llm_client._validate(planner_mod.PlanOut, _fake_resp("{}", parsed=m))
        self.assertIs(out, m)

    def test_validate_basemodel_from_text(self):
        text = json.dumps({"language": "ko", "sub_queries": [{"query": "q1"}]})
        out = llm_client._validate(planner_mod.PlanOut, _fake_resp(text))
        self.assertEqual(out.sub_queries[0].query, "q1")

    def test_validate_list_schema(self):
        out = llm_client._validate(list[str], _fake_resp('["a", "b"]'))
        self.assertEqual(out, ["a", "b"])

    def test_validate_invalid_raises(self):
        with self.assertRaises(Exception):
            llm_client._validate(planner_mod.PlanOut, _fake_resp("not json"))

    def test_output_tokens_prefers_usage_metadata(self):
        self.assertEqual(llm_client._output_tokens(_fake_resp("xxxx" * 100, out_tokens=7)), 7)
        self.assertEqual(llm_client._output_tokens(_fake_resp("xxxx" * 100)), 100)

    def test_unavailable_returns_none(self):
        orig = llm_client.available
        llm_client.available = lambda: False
        try:
            res = _run(llm_client.generate_structured("p", planner_mod.PlanOut, "m"))
            self.assertIsNone(res)
        finally:
            llm_client.available = orig


class TestPlannerStructured(_Patched):

    def test_structured_path_builds_plan(self):
        """구조화 성공 → 레거시 모델을 아예 만들지 않고 계획 생성."""
        plan_out = planner_mod.PlanOut(
            language="en",
            sub_queries=[planner_mod.SubQueryOut(query="INDI Wuxi stake sale", priority=1)],
            required_sections=["개요"],
            search_strategy="s",
            coverage_gaps=["SZSE 원문"],
        )
        self._set_structured(StructuredResult(data=plan_out, output_tokens=42))
        p = planner_mod.Planner()
        p._get_model = lambda: self.fail("구조화 성공 시 레거시 모델 호출 금지")  # noqa
        plan = _run(p.plan("INDI 분석"))
        self.assertEqual(plan.sub_queries[0].query, "INDI Wuxi stake sale")
        self.assertEqual(plan.coverage_gaps, ["SZSE 원문"])
        self.assertEqual(p.tokens_used, 42)

    def test_legacy_fallback_when_structured_none(self):
        """구조화 실패(None) → 레거시 자유텍스트 경로가 그대로 동작."""
        self._set_structured(None)
        legacy = _FakeLegacyModel(json.dumps({
            "language": "ko",
            "sub_queries": [{"query": "legacy q", "priority": 1}],
            "required_sections": ["개요"],
        }))
        p = planner_mod.Planner()
        p._model = legacy
        plan = _run(p.plan("test"))
        self.assertEqual(legacy.calls, 1)
        self.assertEqual(plan.sub_queries[0].query, "legacy q")

    def test_both_fail_uses_fallback_plan(self):
        self._set_structured(None)
        p = planner_mod.Planner()
        p._get_model = lambda: None
        plan = _run(p.plan("q"))
        self.assertTrue(plan.sub_queries)  # 기본 계획
        self.assertEqual(plan.search_strategy, "기본 병렬 검색")


def _contents():
    return [ExtractedContent(url=f"https://ex.com/{i}", title=f"t{i}",
                             content="본문 텍스트입니다.", domain="ex.com")
            for i in range(6)]


def _plan():
    return ResearchPlan(
        original_query="q", sub_queries=[SubQuery(query="q")],
        required_sections=["개요"],
    )


class TestCriticStructured(_Patched):

    def test_structured_path_maps_gap_analysis(self):
        gap = critic_mod.GapOut(
            is_sufficient=True, confidence=0.9, gaps=["g1"],
            additional_queries=[critic_mod.AdditionalQueryOut(query="aq")],
            reasoning="ok",
        )
        self._set_structured(StructuredResult(data=gap, output_tokens=10))
        c = critic_mod.Critic()
        c._get_model = lambda: self.fail("구조화 성공 시 레거시 모델 호출 금지")  # noqa
        res = _run(c.evaluate(_plan(), _contents(), iteration=2))
        self.assertTrue(res.is_sufficient)
        self.assertEqual(res.confidence, 0.9)
        self.assertEqual(res.additional_queries[0].query, "aq")

    def test_iteration1_force_applies_to_structured(self):
        """1회차 confidence<0.85 강제 보완 규칙이 구조화 경로에도 적용."""
        gap = critic_mod.GapOut(is_sufficient=True, confidence=0.7)
        self._set_structured(StructuredResult(data=gap, output_tokens=1))
        c = critic_mod.Critic()
        res = _run(c.evaluate(_plan(), _contents(), iteration=1))
        self.assertFalse(res.is_sufficient)

    def test_legacy_fallback_when_structured_none(self):
        self._set_structured(None)
        legacy = _FakeLegacyModel(json.dumps({
            "is_sufficient": True, "confidence": 0.95,
            "gaps": [], "additional_queries": [], "reasoning": "r",
        }))
        c = critic_mod.Critic()
        c._model = legacy
        res = _run(c.evaluate(_plan(), _contents(), iteration=2))
        self.assertEqual(legacy.calls, 1)
        self.assertTrue(res.is_sufficient)


class TestSynthesizerStructured(_Patched):

    def test_extract_metadata_structured(self):
        meta = synth_mod.MetadataOut(
            timeline=[synth_mod.TimelineOut(date="2026-01-01", event="e")],
            key_findings=[synth_mod.FindingOut(finding="f1", confidence="high"),
                          synth_mod.FindingOut(finding="f2", confidence="low")],
            coverage=synth_mod.CoverageOut(checked=["SEC"], notes="n"),
        )
        self._set_structured(StructuredResult(data=meta, output_tokens=5))
        s = synth_mod.Synthesizer()
        out = _run(s._extract_metadata("## 보고서"))
        self.assertEqual(len(out["key_findings"]), 2)
        self.assertEqual(out["key_findings"][0]["confidence"], "high")
        self.assertEqual(out["coverage"]["checked"], ["SEC"])

    def test_extract_metadata_legacy_fallback(self):
        self._set_structured(None)
        legacy = _FakeLegacyModel(json.dumps(
            {"timeline": [], "key_findings": [{"finding": "lf"}], "coverage": {}}
        ))
        s = synth_mod.Synthesizer()
        s._get_extract_model = lambda: legacy
        out = _run(s._extract_metadata("## 보고서"))
        self.assertEqual(out["key_findings"][0]["finding"], "lf")

    def test_self_verify_structured_replaces(self):
        verified = synth_mod.VerifiedReportOut(
            summary="검증된 요약",
            sections=[synth_mod.SectionOut(title="t", content="c")],
        )
        self._set_structured(StructuredResult(data=verified, output_tokens=3))
        s = synth_mod.Synthesizer()
        storage = SimpleNamespace(all_texts_combined=lambda max_chars: "원본")
        out = _run(s._self_verify({"summary": "old"}, storage, None))
        self.assertEqual(out["summary"], "검증된 요약")

    def test_self_verify_empty_shell_keeps_original(self):
        """검증 패스가 전 필드 기본값(빈 보고서)을 반환하면 원본 유지."""
        self._set_structured(StructuredResult(data=synth_mod.VerifiedReportOut(), output_tokens=0))
        s = synth_mod.Synthesizer()
        storage = SimpleNamespace(all_texts_combined=lambda max_chars: "원본")
        original = {"summary": "old", "sections": [{"title": "t", "content": "c"}]}
        out = _run(s._self_verify(original, storage, None))
        self.assertIs(out, original)


class TestLeadFollowerStructured(_Patched):

    def test_structured_leads_filtered_by_visited(self):
        self._set_structured(StructuredResult(
            data=["INDI SEC 8-K Wuxi stake sale", "visited lead", "  "],
            output_tokens=2,
        ))
        f = lead_mod.LeadFollower()
        f._get_model = lambda: self.fail("구조화 성공 시 레거시 모델 호출 금지")  # noqa
        leads = _run(f._extract_leads(
            "indie Semiconductor announced Wuxi stake sale in SEC 8-K",
            "INDI Wuxi", visited={"visited lead"}, k=3,
        ))
        self.assertIn("INDI SEC 8-K Wuxi stake sale", leads)
        self.assertNotIn("visited lead", leads)

    def test_legacy_fallback_when_structured_none(self):
        self._set_structured(None)
        # 단서 랭커는 수집 텍스트에 근거한 단서만 통과시킨다(무할루시네이션) —
        # 텍스트에 실제 등장하는 토큰으로 구성
        legacy = _FakeLegacyModel('["INDI SEC 8-K Wuxi stake sale"]')
        f = lead_mod.LeadFollower()
        f._model = legacy
        leads = _run(f._extract_leads(
            "indie Semiconductor announced Wuxi stake sale in SEC 8-K",
            "INDI Wuxi", visited=set(), k=3,
        ))
        self.assertEqual(legacy.calls, 1)
        self.assertIn("INDI SEC 8-K Wuxi stake sale", leads)


if __name__ == "__main__":
    unittest.main(verbosity=2)
