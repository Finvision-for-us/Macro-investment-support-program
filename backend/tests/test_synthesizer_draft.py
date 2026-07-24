"""② 초안/심사본 공용 조립기(_assemble_response) 단위테스트 — network/LLM 없음.

핵심 계약:
- 같은 data로 초안(status=RUNNING, cross_validation=[])과 심사본(DONE, cross_val 有) 생성.
- 타임라인은 날짜순 정렬, 커버리지는 pipeline+LLM 병합.
- 병합 순수성: 2회 호출해도 pipeline_coverage 원본 불변·이중 병합 없음.

실행: python backend/tests/test_synthesizer_draft.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents.synthesizer import Synthesizer
from app.deep_research.models import (
    CoverageInfo, JobStatus, ResearchMetadata, SourceInfo,
)


def _data():
    return {
        "summary": "요약 [1]",
        "sections": [{"title": "재무", "content": "매출 성장 [1]",
                      "sources": ["https://sec.gov/a"]}],
        "timeline": [{"date": "2026-05", "event": "실적 발표", "source": ""},
                     {"date": "2026-02", "event": "첫 출하", "source": ""}],
        "key_findings": [{"finding": "수주잔고 74억 달러", "confidence": "high",
                          "sources": []}],
        "coverage": {"checked": ["SEC"], "unchecked": ["중국 공시"],
                     "notes": "LLM노트"},
    }


def _sources():
    return [SourceInfo(url="https://sec.gov/a", title="t", domain="sec.gov",
                       ref_number=1)]


class TestAssembleResponse(unittest.TestCase):

    def setUp(self):
        self.s = Synthesizer()
        self.pcov = CoverageInfo(checked=["관할:US"], unchecked=[],
                                 notes="파이프라인노트")

    def test_draft_shape(self):
        md = ResearchMetadata()
        draft = self.s._assemble_response(
            query="q", job_id="j", data=_data(), all_sources=_sources(),
            pipeline_coverage=self.pcov, unverified_gaps=["gap1"],
            cross_validation=[], metadata=md, status=JobStatus.RUNNING)
        self.assertEqual(draft.status, JobStatus.RUNNING)
        self.assertEqual(draft.cross_validation, [])
        self.assertEqual(draft.summary, "요약 [1]")
        self.assertEqual(len(draft.sections), 1)
        self.assertEqual(draft.unverified_gaps, ["gap1"])
        # 타임라인 날짜순 정렬
        self.assertEqual([t.date for t in draft.timeline], ["2026-02", "2026-05"])
        self.assertEqual(md.total_sources, 1)

    def test_coverage_merge(self):
        draft = self.s._assemble_response(
            query="q", job_id="j", data=_data(), all_sources=_sources(),
            pipeline_coverage=self.pcov, unverified_gaps=[],
            cross_validation=[], metadata=ResearchMetadata(),
            status=JobStatus.RUNNING)
        self.assertIn("관할:US", draft.coverage.checked)   # pipeline
        self.assertIn("SEC", draft.coverage.checked)        # LLM
        self.assertEqual(draft.coverage.notes, "파이프라인노트 | LLM노트")

    def test_merge_purity_no_double(self):
        """2회 호출(초안→심사본)해도 원본 불변·이중 병합 없음."""
        d = _data()
        self.s._assemble_response(
            query="q", job_id="j", data=d, all_sources=_sources(),
            pipeline_coverage=self.pcov, unverified_gaps=[],
            cross_validation=[], metadata=ResearchMetadata(),
            status=JobStatus.RUNNING)
        final = self.s._assemble_response(
            query="q", job_id="j", data=d, all_sources=_sources(),
            pipeline_coverage=self.pcov, unverified_gaps=[],
            cross_validation=["[원장 일치] 현금 174M"], metadata=ResearchMetadata(),
            status=JobStatus.DONE)
        self.assertEqual(final.status, JobStatus.DONE)
        self.assertTrue(final.cross_validation)
        # 노트가 한 번만 병합됨 (이중 " | LLM노트 | LLM노트" 아님)
        self.assertEqual(final.coverage.notes, "파이프라인노트 | LLM노트")
        # pipeline_coverage 원본 불변
        self.assertEqual(self.pcov.notes, "파이프라인노트")
        self.assertEqual(self.pcov.checked, ["관할:US"])

    def test_broken_finding_skipped_not_fatal(self):
        d = _data()
        d["key_findings"].append({"finding": "정상2", "confidence": "weird",
                                  "sources": None})
        out = self.s._assemble_response(
            query="q", job_id="j", data=d, all_sources=_sources(),
            pipeline_coverage=None, unverified_gaps=[], cross_validation=[],
            metadata=ResearchMetadata(), status=JobStatus.DONE)
        # 이상 confidence도 MEDIUM으로 흡수돼 스킵되지 않음
        self.assertEqual(len(out.key_findings), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
