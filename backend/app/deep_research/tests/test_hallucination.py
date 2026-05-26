"""
환각 방어 검증 테스트 3종

실행: python3 -m app.deep_research.tests.test_hallucination
또는: python3 app/deep_research/tests/test_hallucination.py

테스트 4: 출처 없는 정보 차단 — 소스 부족 시 LLM 사전지식 보충 여부
테스트 5: 인용 검증 — 직접 인용문이 원본에 실제 존재하는지
테스트 6: 추론 비약 감지 — 결론 문장에 [추론] 태그 누락 여부
"""
from __future__ import annotations
import re
import sys
import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.WARNING)

# ── 결과 구조 ──────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    score: float          # 0.0 ~ 1.0
    details: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def print(self):
        icon = "✅" if self.passed else "❌"
        print(f"\n{icon} [{self.name}] score={self.score:.0%}")
        for d in self.details:
            print(f"   {d}")
        for f in self.failures:
            print(f"   ⚠ {f}")


# ── 패턴 상수 ──────────────────────────────────────────────

_SOURCE_TAG = re.compile(r'\[source:\s*https?://[^\]]+\]', re.IGNORECASE)
_QUOTE_PATTERN = re.compile(r'["""](.{10,200}?)["""]')
_UNVERIFIED_TAG = re.compile(r'\[unverified\]', re.IGNORECASE)
_INFERENCE_TAG = re.compile(r'\[추론\]')

# 결론성 문장 패턴 (한국어 + 영어)
_CONCLUSION_PATTERNS = re.compile(
    r'(따라서|결론적으로|이에 따라|전망된다|예상된다|것으로 보인다|'
    r'할 것으로|될 것으로|이를 통해|이는.*의미|전망이다|'
    r'therefore|thus|consequently|is expected to|is likely to|'
    r'will likely|suggests that|indicates that)',
    re.IGNORECASE
)

# 핵심 데이터 포함 문장 패턴 (숫자, 날짜, 인물명)
_HARD_FACT_PATTERN = re.compile(
    r'\$[\d,\.]+[BMKbmk]?'
    r'|\d+\.?\d*\s*%'
    r'|\d{4}-\d{2}-\d{2}'
    r'|\d{4}년\s*\d{1,2}월'
    r'|\b(CEO|CFO|COO|CTO)\b'
    r'|\d+\.?\d*\s*(?:억|조|billion|million)',
    re.IGNORECASE
)


# ── 테스트 4: 출처 없는 정보 차단 ─────────────────────────

def test4_empty_source_blocking(report: dict, raw_sources: list[str]) -> TestResult:
    """
    소스가 없거나 부족할 때 LLM이 사전 지식으로 보충했는지 감지.

    통과 기준:
    - 핵심 데이터(숫자/날짜) 포함 문장 중 [source:] 없는 비율 < 20%
    - raw_sources가 비어있는데 보고서가 구체적 수치를 많이 포함하면 실패
    - "정보 부족" 또는 빈 섹션 존재하면 가점
    """
    all_text = _extract_all_text(report)
    sentences = _split_sentences(all_text)

    hard_fact_sentences = [s for s in sentences if _HARD_FACT_PATTERN.search(s)]
    sourced = [s for s in hard_fact_sentences if _SOURCE_TAG.search(s)]
    unsourced = [s for s in hard_fact_sentences if not _SOURCE_TAG.search(s)
                 and not _UNVERIFIED_TAG.search(s)]

    details = [
        f"핵심 데이터 포함 문장: {len(hard_fact_sentences)}개",
        f"출처 있음: {len(sourced)}개",
        f"출처 없음: {len(unsourced)}개",
    ]
    failures = []

    # 소스가 아예 없는데 수치를 많이 쓰면 경고
    if not raw_sources and len(hard_fact_sentences) > 5:
        failures.append(f"원본 소스 없음에도 수치 포함 문장 {len(hard_fact_sentences)}개 — 사전지식 보충 의심")

    # 출처 없는 수치 문장 샘플
    for s in unsourced[:3]:
        failures.append(f"출처 없는 수치: {s[:80]}...")

    # 정보 부족 표시 확인
    has_info_lack = "정보 부족" in all_text or "information not available" in all_text.lower()
    if has_info_lack:
        details.append("'정보 부족' 표시 발견 ✓")

    ratio = len(unsourced) / max(len(hard_fact_sentences), 1)
    # 소스 없을 때 기준 더 엄격
    threshold = 0.10 if not raw_sources else 0.20
    passed = ratio < threshold

    score = max(0.0, 1.0 - ratio)
    return TestResult("테스트4: 출처 없는 정보 차단", passed, score, details, failures)


# ── 테스트 5: 인용 검증 ────────────────────────────────────

def test5_quote_verification(report: dict, raw_sources: list[str]) -> TestResult:
    """
    보고서 내 직접 인용문("...")이 원본 소스 텍스트에 실제로 존재하는지 확인.

    통과 기준:
    - 직접 인용문이 없으면 N/A (통과)
    - 인용문 중 원본에서 확인된 비율 >= 80%
    """
    all_text = _extract_all_text(report)
    quotes = _QUOTE_PATTERN.findall(all_text)

    details = [f"직접 인용문 {len(quotes)}개 발견"]
    failures = []

    if not quotes:
        return TestResult("테스트5: 인용 검증", True, 1.0,
                          ["직접 인용문 없음 — N/A (통과)"], [])

    verified_count = 0
    combined_sources = " ".join(raw_sources).lower()

    for quote in quotes:
        q_lower = quote.lower().strip()
        # 인용문 핵심 단어(3개 이상) 원본에 있는지 확인
        words = [w for w in q_lower.split() if len(w) > 3]
        if not words:
            verified_count += 1
            continue

        # 연속된 3단어가 원본에 있으면 인정
        found = False
        for i in range(len(words) - 2):
            phrase = " ".join(words[i:i+3])
            if phrase in combined_sources:
                found = True
                break

        if found:
            verified_count += 1
        else:
            failures.append(f"원본 미확인 인용: \"{quote[:60]}...\"")

    ratio = verified_count / len(quotes)
    passed = ratio >= 0.80
    details.append(f"검증 통과: {verified_count}/{len(quotes)} ({ratio:.0%})")

    return TestResult("테스트5: 인용 검증", passed, ratio, details, failures)


# ── 테스트 6: 추론 비약 감지 ──────────────────────────────

def test6_inference_leap_detection(report: dict) -> TestResult:
    """
    결론성 문장에 [추론] 태그가 없으면 추론 비약으로 판정.

    통과 기준:
    - 결론성 문장 중 [추론] 태그 있거나 [source:] 직접 인용 있는 비율 >= 75%
    - 다단계 추론 체인(결론 → 결론)이 태그 없이 이어지면 실패
    """
    all_text = _extract_all_text(report)
    sentences = _split_sentences(all_text)

    conclusion_sentences = [s for s in sentences if _CONCLUSION_PATTERNS.search(s)]
    details = [f"결론성 문장: {len(conclusion_sentences)}개"]
    failures = []

    if not conclusion_sentences:
        return TestResult("테스트6: 추론 비약 감지", True, 1.0,
                          ["결론성 문장 없음 — N/A (통과)"], [])

    tagged = []
    untagged = []
    for s in conclusion_sentences:
        has_inference = bool(_INFERENCE_TAG.search(s))
        has_source = bool(_SOURCE_TAG.search(s))
        if has_inference or has_source:
            tagged.append(s)
        else:
            untagged.append(s)
            failures.append(f"[추론] 태그 없는 결론: {s[:80]}...")

    # 연속 추론 비약 감지 (결론 문장이 3개 이상 연속이면서 태그 없음)
    consecutive = 0
    max_consecutive = 0
    for s in sentences:
        if _CONCLUSION_PATTERNS.search(s) and not _INFERENCE_TAG.search(s):
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0

    if max_consecutive >= 3:
        failures.append(f"연속 추론 비약 {max_consecutive}문장 감지 (태그 없음)")

    ratio = len(tagged) / len(conclusion_sentences)
    passed = ratio >= 0.75 and max_consecutive < 3
    details.append(f"태그 있음: {len(tagged)}/{len(conclusion_sentences)} ({ratio:.0%})")

    return TestResult("테스트6: 추론 비약 감지", passed, ratio, details, failures)


# ── 종합 실행 ──────────────────────────────────────────────

def run_all(report: dict, raw_sources: list[str] = None) -> dict:
    """세 테스트를 모두 실행하고 종합 결과 반환."""
    raw_sources = raw_sources or []

    results = [
        test4_empty_source_blocking(report, raw_sources),
        test5_quote_verification(report, raw_sources),
        test6_inference_leap_detection(report),
    ]

    print("\n" + "="*55)
    print("  FinVision 환각 방어 검증 테스트")
    print("="*55)

    for r in results:
        r.print()

    passed_count = sum(1 for r in results if r.passed)
    avg_score = sum(r.score for r in results) / len(results)

    print("\n" + "-"*55)
    print(f"  결과: {passed_count}/{len(results)} 통과  |  평균 점수: {avg_score:.0%}")

    if passed_count == len(results):
        print("  판정: ✅ 환각 방어 정상 작동")
    elif passed_count >= 2:
        print("  판정: ⚠️  부분 통과 — 실패 항목 점검 필요")
    else:
        print("  판정: ❌ 환각 방어 취약 — 즉시 수정 필요")
    print("="*55)

    return {
        "passed": passed_count,
        "total": len(results),
        "score": avg_score,
        "results": [{"name": r.name, "passed": r.passed, "score": r.score,
                     "failures": r.failures} for r in results],
    }


# ── 헬퍼 ──────────────────────────────────────────────────

def _extract_all_text(report: dict) -> str:
    parts = [report.get("summary", "")]
    for s in report.get("sections", []):
        parts.append(s.get("content", ""))
    for f in report.get("key_findings", []):
        finding = f.get("finding", "") if isinstance(f, dict) else str(f)
        parts.append(finding)
    for t in report.get("timeline", []):
        parts.append(t.get("event", "") if isinstance(t, dict) else str(t))
    return " ".join(parts)


def _split_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.。!?])\s+|(?<=\n)', text)
    return [s.strip() for s in sentences if len(s.strip()) > 15]


# ── 샘플 테스트 (직접 실행 시) ────────────────────────────

_SAMPLE_REPORT_GOOD = {
    "summary": "NVDA는 2024년 3분기 매출 $35.1B을 기록했습니다 [source: https://ir.nvidia.com/q3-2024]. [추론] 이는 AI 수요 증가에 따른 것으로 보입니다.",
    "sections": [
        {
            "title": "재무 현황",
            "content": "데이터센터 매출은 $30.8B으로 전년 대비 112% 성장했습니다 [source: https://ir.nvidia.com/q3-2024]. 정보 부족: 유럽 지역별 세부 매출은 확인되지 않습니다.",
        }
    ],
    "key_findings": [
        {"finding": "매출 $35.1B — 시장 예상치 상회 [source: https://ir.nvidia.com/q3-2024]", "confidence": "high", "sources": ["https://ir.nvidia.com/q3-2024"]},
    ],
    "timeline": [
        {"date": "2024-11-20", "event": "Q3 실적 발표 — 매출 $35.1B [source: https://ir.nvidia.com/q3-2024]", "source": "https://ir.nvidia.com/q3-2024"}
    ],
}

_SAMPLE_REPORT_BAD = {
    "summary": "NVDA는 2024년 3분기 매출 $35.1B을 기록했습니다. Jensen Huang CEO는 \"AI 수요가 폭발적\"이라고 말했습니다. 따라서 주가는 계속 상승할 것입니다. 이에 따라 투자 매력이 높아질 것으로 전망된다. 결론적으로 매수 추천입니다.",
    "sections": [
        {
            "title": "재무 현황",
            "content": "데이터센터 매출은 $30.8B으로 112% 성장했습니다. 이를 통해 시장 지배력이 강화되었습니다. 따라서 경쟁사 대비 우위가 지속될 것입니다.",
        }
    ],
    "key_findings": [
        {"finding": "AI 칩 시장 점유율 80% 이상으로 추정됨", "confidence": "high", "sources": []},
    ],
    "timeline": [],
}

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="환각 방어 검증 테스트")
    parser.add_argument("--report", help="JSON 보고서 파일 경로 (없으면 샘플 사용)")
    parser.add_argument("--sources", help="원본 소스 텍스트 파일 경로")
    parser.add_argument("--mode", choices=["good", "bad", "file"], default="bad",
                        help="good=양호 샘플, bad=불량 샘플, file=파일 사용")
    args = parser.parse_args()

    if args.mode == "file" and args.report:
        with open(args.report) as f:
            report = json.load(f)
        sources = []
        if args.sources:
            with open(args.sources) as f:
                sources = [f.read()]
    elif args.mode == "good":
        report = _SAMPLE_REPORT_GOOD
        sources = ["NVDA reported revenue of $35.1B in Q3 2024. Data center revenue reached $30.8B, up 112% year-over-year. Jensen Huang stated AI demand is explosive."]
    else:
        report = _SAMPLE_REPORT_BAD
        sources = []

    print(f"\n테스트 모드: {args.mode.upper()}")
    run_all(report, sources)
