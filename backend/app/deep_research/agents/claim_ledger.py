"""최종 핵심 주장에 검증 결과를 연결하는 결정론적 Claim Safety Ledger."""
from __future__ import annotations

import hashlib
import re

from app.deep_research.agents.cross_checker import cross_checker
from app.deep_research.agents.source_matcher import source_matcher
from app.deep_research.models import ClaimRecord, ConfidenceLevel


_INFERENCE_RE = re.compile(r"\[추론\]", re.IGNORECASE)
_UNVERIFIED_RE = re.compile(
    r"\[\[?unverified\]?\]|\[추가\s*검증\s*필요\]|\[source:\s*미확인\]",
    re.IGNORECASE,
)
_GUIDANCE_RE = re.compile(
    r"가이던스|전망했|예상했|목표로|계획이|경영진|회사는\s+.*(?:전망|예상|계획)",
    re.IGNORECASE,
)
_EXTERNAL_FORECAST_RE = re.compile(
    r"애널리스트|컨센서스|조사기관|전망치|목표주가|예상한다",
    re.IGNORECASE,
)
_CALCULATION_RE = re.compile(
    r"FinVision|자체\s*계산|민감도|산식|계산하면|시나리오",
    re.IGNORECASE,
)


def _claim_type(text: str) -> str:
    if _INFERENCE_RE.search(text):
        return "finvision_interpretation"
    if _CALCULATION_RE.search(text):
        return "finvision_calculation"
    if _GUIDANCE_RE.search(text):
        return "management_guidance"
    if _EXTERNAL_FORECAST_RE.search(text):
        return "external_forecast"
    return "confirmed_fact"


def _claim_id(run_id: str, index: int, text: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{index}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"claim_{digest}"


def build_claim_ledger(
    research_run_id: str,
    findings: list[dict],
    raw_sources,
) -> list[ClaimRecord]:
    """최종 key_findings만 검증한다. 중간 후보·다른 run 상태는 입력받지 않는다."""
    sources = raw_sources.all_sources() if raw_sources and len(raw_sources) else []
    source_texts = [s.text for s in sources]
    ledger: list[ClaimRecord] = []

    for index, finding in enumerate(findings):
        text = (finding.get("finding") or "").strip()
        if not text:
            continue

        declared_sources = list(dict.fromkeys(finding.get("sources") or []))
        confidence_raw = str(finding.get("confidence") or "medium").lower()
        confidence = (
            ConfidenceLevel(confidence_raw)
            if confidence_raw in {"high", "medium", "low"}
            else ConfidenceLevel.MEDIUM
        )
        kind = _claim_type(text)

        matched = source_matcher.verify_claim(text, source_texts)
        crossed = cross_checker.cross_check(text, sources)
        agreeing = list(dict.fromkeys(crossed.get("agreeing_sources") or []))
        conflicts = crossed.get("conflicting_sources") or []
        evidence_sources = list(dict.fromkeys(declared_sources + agreeing))

        if _UNVERIFIED_RE.search(text):
            status = "unverified"
        elif conflicts:
            status = "contradicted"
        elif matched.get("verified") and crossed.get("recommendation") == "include":
            status = "verified"
        elif matched.get("verified") and len(agreeing) == 1:
            status = "single_source"
        elif matched.get("verified"):
            status = "partially_verified"
        else:
            status = "unverified"

        eligible_types = {
            "confirmed_fact",
            "management_guidance",
            "external_forecast",
            "finvision_calculation",
        }
        executive_eligible = (
            kind in eligible_types
            and status == "verified"
            and confidence in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}
        )
        # 외부 전망과 자체 계산은 더 강한 기준을 적용한다.
        if kind in {"external_forecast", "finvision_calculation"}:
            executive_eligible = executive_eligible and confidence == ConfidenceLevel.HIGH

        ledger.append(ClaimRecord(
            claim_id=_claim_id(research_run_id, index, text),
            research_run_id=research_run_id,
            claim_text=text,
            claim_type=kind,
            confidence=confidence,
            verification_status=status,
            source_ids=evidence_sources,
            evidence_excerpt=matched.get("matched_excerpt"),
            counter_evidence=[
                f"{item.get('url', '')}: {item.get('note', '상충 근거')}".strip()
                for item in conflicts
            ],
            executive_summary_eligible=executive_eligible,
        ))

    return ledger
