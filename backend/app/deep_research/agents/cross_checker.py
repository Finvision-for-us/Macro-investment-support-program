"""방어선 4: Multi-Source Cross-Checker — 같은 fact가 여러 출처에서 일치하는지 확인."""
from __future__ import annotations
import re
import logging
from urllib.parse import urlparse

from app.deep_research.agents.source_matcher import _extract_key_facts, _fuzzy_match
from app.deep_research.storage.raw_sources import RawSource

logger = logging.getLogger(__name__)

# 출처 신뢰도 가중치 (높을수록 신뢰)
_DOMAIN_WEIGHT: dict[str, int] = {
    # Tier 1 — 규제 공시
    "sec.gov": 10, "dart.fss.or.kr": 10, "fred.stlouisfed.org": 9,
    "szse.cn": 10, "szse.com.cn": 10, "sse.com.cn": 10,
    "csrc.gov.cn": 10, "fsc.go.kr": 10, "ec.europa.eu": 9,
    # Tier 2 — Tier-1 미디어
    "reuters.com": 8, "apnews.com": 8, "bloomberg.com": 7,
    "ft.com": 7, "wsj.com": 7, "nikkei.com": 7,
    # Tier 3 — 전문 분석
    "arxiv.org": 8, "cnbc.com": 6, "marketwatch.com": 6,
    "techcrunch.com": 5,
    # Tier 4 — 자동생성/블로그 (가중치 1)
    "stockinsights.ai": 1, "pitchgrade.com": 1,
    "stockanalysis.com": 2, "simplywall.st": 1,
    "wisesheets.io": 1, "stockstory.org": 1,
    "finviz.com": 2, "macrotrends.net": 2,
}

# Tier 4 저품질 도메인 집합 (빠른 조회용)
_LOW_QUALITY_DOMAINS = frozenset([
    "stockinsights.ai", "pitchgrade.com", "simplywall.st",
    "wisesheets.io", "stockstory.org",
])

def _domain_weight(url: str) -> int:
    try:
        domain = urlparse(url).netloc.lstrip("www.")
    except Exception:
        domain = url
    for key, w in _DOMAIN_WEIGHT.items():
        if key in domain:
            return w
    if "gov" in domain or "edu" in domain:
        return 7
    return 2  # default 3 → 2 (미검증 출처 기본값 하향)


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


def _find_contradicting_numbers(fact: str, source_text: str) -> list[str]:
    """fact에 있는 숫자와 비슷한 문맥의 다른 숫자가 출처에 있는지 탐지."""
    fact_nums = re.findall(r'\d+\.?\d*', fact)
    if not fact_nums:
        return []

    # 숫자 앞 문맥어 추출 (예: "revenue", "price" 등)
    context_words = re.findall(r'[a-zA-Z가-힣]+', fact)[:3]
    contradictions = []

    for word in context_words:
        pattern = re.compile(
            rf'{re.escape(word)}\s*[:\s]\s*([\$\d,\.]+\s*[BMKbmk%억조만]*)',
            re.IGNORECASE
        )
        for m in pattern.finditer(source_text):
            found = m.group(1).strip()
            # 동일한 숫자 아니면 모순 후보
            found_clean = re.sub(r'[,\s]', '', found)
            for fn in fact_nums:
                if found_clean and found_clean != fn and found_clean not in fact:
                    contradictions.append(found)
    return contradictions[:2]


# 싱글톤
cross_checker = MultiSourceCrossChecker()
