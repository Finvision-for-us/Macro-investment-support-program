"""방어선 4: Multi-Source Cross-Checker — 같은 fact가 여러 출처에서 일치하는지 확인."""
from __future__ import annotations
import re
import logging
from urllib.parse import urlparse

from app.deep_research.agents.source_matcher import _extract_key_facts, _fuzzy_match
from app.deep_research.sources.source_registry import (
    get_domain_weight, LOW_TRUST_DOMAINS,
)
from app.deep_research.storage.raw_sources import RawSource

logger = logging.getLogger(__name__)

# 신뢰도 가중치는 source_registry가 단일 진실 소스 —
# 이전의 로컬 _DOMAIN_WEIGHT 하드코딩은 다른 4곳과 어긋나 있었다(wsj=7 vs high vs medium).
_LOW_QUALITY_DOMAINS = LOW_TRUST_DOMAINS  # 하위호환 alias

def _domain_weight(url: str) -> int:
    try:
        domain = urlparse(url).netloc.removeprefix("www.")
    except Exception:
        domain = url
    return get_domain_weight(domain)


class MultiSourceCrossChecker:
    """같은 주장을 여러 출처와 교차 검증."""

    def cross_check(
        self,
        claim: str,
        sources: list[RawSource],
        threshold: float = 0.65,
    ) -> dict:
        """
        반환:
        {
            "confidence": "high" | "medium" | "low",
            "agreeing_sources": [url],
            "conflicting_sources": [{url, note}],
            "recommendation": "include" | "tag" | "exclude",
            "weight_score": float
        }
        """
        if not claim.strip() or not sources:
            return {"confidence": "low", "agreeing_sources": [],
                    "conflicting_sources": [], "recommendation": "tag",
                    "weight_score": 0.0}

        key_facts = _extract_key_facts(claim)
        agreeing: list[str] = []
        conflicting: list[dict] = []
        total_weight = 0.0

        for src in sources:
            matched, score, _ = _fuzzy_match(claim, src.text, threshold)
            w = _domain_weight(src.url)

            if matched:
                agreeing.append(src.url)
                total_weight += w
            elif key_facts:
                # 핵심 수치가 다른 값으로 나타나는지 확인
                src_text_l = src.text.lower()
                for fact in key_facts:
                    if _is_numeric_fact(fact):
                        contradictions = _find_contradicting_numbers(fact, src.text)
                        if contradictions:
                            conflicting.append({"url": src.url, "note": f"다른 수치: {contradictions}"})
                            break

        # 신뢰도 결정
        agree_count = len(agreeing)
        if agree_count >= 3 or total_weight >= 15:
            confidence = "high"
            recommendation = "include"
        elif agree_count >= 1 or total_weight >= 6:
            confidence = "medium"
            recommendation = "include" if not conflicting else "tag"
        else:
            confidence = "low"
            recommendation = "tag" if not conflicting else "exclude"

        return {
            "confidence": confidence,
            "agreeing_sources": agreeing,
            "conflicting_sources": conflicting,
            "recommendation": recommendation,
            "weight_score": total_weight,
        }

    def batch_check(
        self,
        claims: list[str],
        sources: list[RawSource],
    ) -> list[dict]:
        return [self.cross_check(c, sources) for c in claims]


def _is_numeric_fact(fact: str) -> bool:
    return bool(re.search(r'\d', fact))


def _numeric_values(text: str) -> list[float]:
    """콤마 포함 숫자를 파편화하지 않고 값(float) 리스트로. '960,834,355' → 960834355.0"""
    out: list[float] = []
    for tok in re.findall(r'[\d,]+(?:\.\d+)?', text):
        s = tok.replace(",", "")
        if s and s != ".":
            try:
                out.append(float(s))
            except ValueError:
                pass
    return out


def _find_contradicting_numbers(fact: str, source_text: str) -> list[str]:
    """fact의 수치와 같은 문맥에서 '다른 값'을 가진 숫자가 출처에 있는지 탐지.

    콤마 숫자를 파편화하지 않고(버그: '960,834,355'→['960','834','355']),
    부분문자열이 아니라 값으로 비교한다.
    """
    fact_vals = _numeric_values(fact)
    if not fact_vals:
        return []

    # 숫자 앞 문맥어 추출 (예: "revenue", "price" 등)
    context_words = [w for w in re.findall(r'[a-zA-Z가-힣]+', fact) if len(w) >= 2][:3]
    contradictions: list[str] = []
    seen: set[str] = set()

    for word in context_words:
        pattern = re.compile(
            rf'{re.escape(word)}\s*[:\s]\s*(\$?\s?[\d,]+(?:\.\d+)?\s*[BMKbmk%억兆조万萬]*)',
            re.IGNORECASE
        )
        for m in pattern.finditer(source_text):
            found = m.group(1).strip()
            for v in _numeric_values(found):
                # fact의 어떤 값과도 '같지 않은'(허용오차 밖) 값이면 모순 후보
                if all(abs(v - fv) > max(1.0, abs(fv) * 0.005) for fv in fact_vals):
                    if found not in seen:
                        seen.add(found)
                        contradictions.append(found)
    return contradictions[:2]


# 싱글톤
cross_checker = MultiSourceCrossChecker()
