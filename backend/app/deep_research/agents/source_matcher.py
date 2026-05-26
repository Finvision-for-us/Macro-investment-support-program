"""방어선 2: Source-Claim Matcher — LLM 주장이 원본에 실제로 있는지 검증."""
from __future__ import annotations
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 숫자/날짜/퍼센트 패턴
_NUM_PATTERN = re.compile(
    r'\$[\d,\.]+[BMKbmk]?'          # 달러 금액
    r'|\d+\.?\d*\s*%'               # 퍼센트
    r'|\d{4}-\d{2}-\d{2}'           # ISO 날짜
    r'|\d{4}년\s*\d{1,2}월'         # 한국 날짜
    r'|\d+\.?\d*\s*(?:억|조|만|billion|million|trillion)'  # 단위
    r'|\b\d{4}\b'                   # 연도
)

# 직접 인용 패턴
_QUOTE_PATTERN = re.compile(r'["""](.+?)["""]')

# 연도 단독 패턴 (4자리 숫자만)
_YEAR_RE = re.compile(r'^\d{4}$')


def _normalize_number(s: str) -> str:
    """통화기호·콤마·단위 약어 정규화. 표기 차이(B vs billion, $ 유무 등) 제거."""
    s = s.strip().lower()
    s = re.sub(r'[$₩€£¥]', '', s)
    s = re.sub(r'(?<=\d),(?=\d)', '', s)
    s = re.sub(r'\s*\btrillion\b', 't', s)
    s = re.sub(r'\s*\bbillion\b|\s*십억\b', 'b', s)
    s = re.sub(r'\s*\bmillion\b|\s*백만\b', 'm', s)
    s = re.sub(r'\s*\bthousand\b', 'k', s)
    s = re.sub(r'\s*(억|조|만)\b', r'\1', s)
    s = re.sub(r'\s+', '', s)
    return s


def _is_year_only(fact: str) -> bool:
    """연도 단독 패턴 여부 (4자리 숫자만)."""
    return bool(_YEAR_RE.match(fact.strip()))


def _extract_key_facts(text: str) -> list[str]:
    """숫자, 날짜, 인용문 추출."""
    facts = []
    facts.extend(_NUM_PATTERN.findall(text))
    facts.extend(m.group(1) for m in _QUOTE_PATTERN.finditer(text))
    return [f.strip() for f in facts if len(f.strip()) >= 2]


def _fuzzy_match(needle: str, haystack: str, threshold: float = 0.75) -> tuple[bool, float, str]:
    """rapidfuzz 기반 퍼지 매칭. 설치 없으면 substring 폴백."""
    needle_l = needle.lower()
    haystack_l = haystack.lower()

    # 정확 포함 먼저
    if needle_l in haystack_l:
        idx = haystack_l.find(needle_l)
        excerpt = haystack[max(0, idx-30):idx+len(needle)+30].strip()
        return True, 1.0, excerpt

    try:
        from rapidfuzz import fuzz, process
        # 문장 단위로 분할해서 가장 유사한 것 찾기
        sentences = re.split(r'[.。\n]', haystack)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if not sentences:
            return False, 0.0, ""
        best = process.extractOne(needle, sentences, scorer=fuzz.partial_ratio)
        if best and best[1] >= threshold * 100:
            return True, best[1] / 100, best[0]
        return False, (best[1] / 100 if best else 0.0), ""
    except ImportError:
        # rapidfuzz 없으면 단어 겹침으로 폴백
        needle_words = set(needle_l.split())
        haystack_words = set(haystack_l.split())
        if not needle_words:
            return False, 0.0, ""
        overlap = len(needle_words & haystack_words) / len(needle_words)
        return overlap >= threshold, overlap, ""


class SourceClaimMatcher:
    """주장 하나가 원본 텍스트 어딘가에 근거가 있는지 확인."""

    def verify_claim(
        self,
        claim: str,
        source_texts: list[str],
        threshold: float = 0.75,
    ) -> dict:
        """
        반환:
        {
            "verified": bool,
            "match_score": float,
            "matched_excerpt": str | None,
            "method": "exact" | "fuzzy" | "number" | "none",
            "unverified_facts": list[str]  # 원본에 없는 핵심 수치/날짜
        }
        """
        if not claim.strip() or not source_texts:
            return {"verified": False, "match_score": 0.0,
                    "matched_excerpt": None, "method": "none", "unverified_facts": []}

        key_facts = _extract_key_facts(claim)
        unverified_facts: list[str] = []

        # 1. 핵심 수치/날짜 정확 매칭 (정규화 비교 + raw substring 폴백)
        if key_facts:
            all_source_text = " ".join(source_texts)
            source_norm_nums = {
                _normalize_number(n) for n in _NUM_PATTERN.findall(all_source_text)
            }
            for fact in key_facts:
                norm = _normalize_number(fact)
                if norm not in source_norm_nums and fact.lower() not in all_source_text.lower():
                    unverified_facts.append(fact)

            if not unverified_facts:
                # 연도만 매칭 → 우연 일치 가능성 높음 → fuzzy로 넘김
                non_year_verified = [f for f in key_facts if not _is_year_only(f)]
                if non_year_verified:
                    return {"verified": True, "match_score": 1.0,
                            "matched_excerpt": None, "method": "number",
                            "unverified_facts": []}

        # 2. 퍼지 매칭 — 어느 출처에라도 있으면 통과
        best_score = 0.0
        best_excerpt = ""
        for src_text in source_texts:
            matched, score, excerpt = _fuzzy_match(claim, src_text, threshold)
            if matched:
                return {"verified": True, "match_score": score,
                        "matched_excerpt": excerpt, "method": "fuzzy",
                        "unverified_facts": unverified_facts}
            if score > best_score:
                best_score = score
                best_excerpt = excerpt

        return {"verified": False, "match_score": best_score,
                "matched_excerpt": best_excerpt or None,
                "method": "none", "unverified_facts": unverified_facts}

    def batch_verify(
        self,
        claims: list[str],
        source_texts: list[str],
        threshold: float = 0.75,
    ) -> list[dict]:
        return [self.verify_claim(c, source_texts, threshold) for c in claims]


# 싱글톤
source_matcher = SourceClaimMatcher()
