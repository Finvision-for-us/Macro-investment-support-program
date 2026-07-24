"""보고서 후처리(방어선 6) 단위테스트 — network/LLM 없음.

2026-07-20 INDI 감사에서 확인된 3대 표시 결함을 픽스처로 재현한다:
1. 각주 무결성 — 본문 [n]이 소스 목록에 없는 댕글링 각주 해소/제거.
2. 태그 위치 — "12[unverified]개월" → "[unverified] 12개월".
3. 깨진 문장 잔재 — "규모 of 신규" → "규모 신규" 등 안전 정리, 복구 불가는 보존.

실행: python backend/tests/test_report_sanitizer.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.agents import report_sanitizer as rs
from app.deep_research.models import SourceInfo


def _src(url, num=None):
    return SourceInfo(url=url, title="t", domain="d", ref_number=num)


class TestRelocateTags(unittest.TestCase):

    def test_tag_between_number_and_unit(self):
        """감사 실측: 숫자-단위 사이 태그."""
        out, moved = rs.relocate_misplaced_tags("향후 약 12[unverified]개월 동안")
        self.assertEqual(out, "향후 약 [unverified] 12개월 동안")
        self.assertEqual(moved, 1)

    def test_tag_glued_after_word(self):
        out, moved = rs.relocate_misplaced_tags("계약 기간 12개월[unverified] 예상")
        self.assertEqual(out, "계약 기간 [unverified] 12개월 예상")
        self.assertEqual(moved, 1)

    def test_tag_glued_before_word(self):
        out, _ = rs.relocate_misplaced_tags("[unverified]개월 단위")
        self.assertEqual(out, "[unverified] 개월 단위")

    def test_correct_standalone_tag_unchanged(self):
        text = "[unverified] 매출이 증가했다"
        out, moved = rs.relocate_misplaced_tags(text)
        self.assertEqual(out, text)
        self.assertEqual(moved, 0)

    def test_inference_tag_and_double_bracket(self):
        out, moved = rs.relocate_misplaced_tags("성장률 5[추론]% 및 값[[unverified]]X")
        self.assertIn("[추론] 5%", out)
        self.assertIn("[[unverified]] 값X", out)
        self.assertEqual(moved, 2)


class TestCleanArtifacts(unittest.TestCase):

    def test_latin_filler_between_hangul(self):
        self.assertEqual(rs.clean_broken_artifacts("규모 of 신규 사업"), "규모 신규 사업")

    def test_english_phrase_preserved(self):
        """정상 영문 구(한글에 안 낀)는 보존."""
        text = "the company reported"
        self.assertEqual(rs.clean_broken_artifacts(text), text)

    def test_empty_brackets_and_double_space(self):
        self.assertEqual(
            rs.clean_broken_artifacts("매출  증가 [] 및 () 이익"), "매출 증가 및 이익")

    def test_decimal_point_preserved(self):
        """숫자 소수점은 마침표 정리에서 보존."""
        self.assertEqual(
            rs.clean_broken_artifacts("마진은 50.2% 수준"), "마진은 50.2% 수준")

    def test_unrepairable_garble_untouched(self):
        """복구 불가한 한글 손상은 손대지 않는다(고친 척 금지)."""
        text = "메모머드급 신제품 출시"
        self.assertEqual(rs.clean_broken_artifacts(text), text)

    def test_detect_midline_placeholder(self):
        self.assertEqual(rs.detect_suspects("원본 확인 필요을 구가할 예정"), 1)
        self.assertEqual(rs.detect_suspects("정상 문장입니다"), 0)


class TestFootnoteIntegrity(unittest.TestCase):

    def test_ensure_cited_url_added_to_sources(self):
        """인용된 URL이 목록에 없으면 편입 → 댕글링 각주 방지."""
        sources = [_src("https://sec.gov/a")]
        url_to_num = {"https://sec.gov/a": 1, "https://example.com/eu-40m": 2}
        out = rs.ensure_cited_sources(sources, url_to_num)
        urls = {s.url for s in out}
        self.assertIn("https://example.com/eu-40m", urls)
        self.assertEqual(len(out), 2)

    def test_assign_ref_numbers(self):
        sources = [_src("https://sec.gov/a"), _src("https://example.com/b")]
        url_to_num = {"https://sec.gov/a": 1, "https://example.com/b": 2}
        rs.assign_ref_numbers(sources, url_to_num)
        self.assertEqual(sources[0].ref_number, 1)
        self.assertEqual(sources[1].ref_number, 2)

    def test_dangling_ref_stripped_valid_kept(self):
        """소스 목록에 있는 [1]은 유지, 없는 [12]는 제거."""
        data = {"summary": "매출 성장 [1]. 유럽 €40M 계약 [12].",
                "sections": [], "key_findings": [], "timeline": []}
        sources = [_src("https://sec.gov/a", 1)]
        out, stats = rs.reconcile_and_sanitize(data, sources, {"https://sec.gov/a": 1})
        self.assertIn("[1]", out["summary"])
        self.assertNotIn("[12]", out["summary"])
        self.assertEqual(stats["dangling"], 1)

    def test_lingering_source_token_normalized(self):
        """검증 패스가 되살린 [source:URL] → [n] 정규화(번호 확정 시)."""
        data = {"summary": "현금 보유 [source: https://sec.gov/a] 안정적.",
                "sections": [], "key_findings": [], "timeline": []}
        sources = [_src("https://sec.gov/a", 1)]
        out, _ = rs.reconcile_and_sanitize(data, sources, {"https://sec.gov/a": 1})
        self.assertIn("[1]", out["summary"])
        self.assertNotIn("source:", out["summary"])


class TestOrchestratorAllFields(unittest.TestCase):

    def test_applies_across_fields(self):
        data = {
            "summary": "약 12[unverified]개월 [99] 소요",
            "sections": [{"title": "T", "content": "규모 of 신규 [1]", "sources": []}],
            "key_findings": [{"finding": "성장 5[추론]% 지속", "confidence": "low",
                              "sources": []}],
            "timeline": [{"date": "2025", "event": "출시 [99]", "source": ""}],
        }
        sources = [_src("https://sec.gov/a", 1)]
        out, stats = rs.reconcile_and_sanitize(data, sources, {"https://sec.gov/a": 1})
        self.assertIn("[unverified] 12개월", out["summary"])
        self.assertNotIn("[99]", out["summary"])
        self.assertEqual(out["sections"][0]["content"], "규모 신규 [1]")
        self.assertIn("[추론] 5%", out["key_findings"][0]["finding"])
        self.assertNotIn("[99]", out["timeline"][0]["event"])
        self.assertEqual(stats["dangling"], 2)   # summary [99] + timeline [99]
        self.assertGreaterEqual(stats["relocated"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
