from app.deep_research.agents.claim_ledger import build_claim_ledger
from app.deep_research.storage.raw_sources import RawSourceStorage


def _storage(*items: tuple[str, str]) -> RawSourceStorage:
    storage = RawSourceStorage()
    for idx, (url, text) in enumerate(items):
        storage.store(url, f"source-{idx}", text)
    return storage


def test_claim_has_stable_run_scoped_id_and_evidence():
    raw = _storage(
        ("https://www.sec.gov/filing", "Micron reported quarterly revenue of $25 billion."),
        ("https://www.reuters.com/article", "Micron reported quarterly revenue of $25 billion."),
    )
    findings = [{
        "finding": "Micron reported quarterly revenue of $25 billion.",
        "confidence": "high",
        "sources": ["https://www.sec.gov/filing"],
    }]

    first = build_claim_ledger("run-a", findings, raw)[0]
    second = build_claim_ledger("run-a", findings, raw)[0]
    other_run = build_claim_ledger("run-b", findings, raw)[0]

    assert first.claim_id == second.claim_id
    assert first.claim_id != other_run.claim_id
    assert first.research_run_id == "run-a"
    assert first.verification_status == "verified"
    assert first.executive_summary_eligible is True
    assert "https://www.sec.gov/filing" in first.source_ids


def test_unverified_claim_is_never_executive_eligible():
    raw = _storage(("https://example.com/source", "Unrelated source text."))
    findings = [{
        "finding": "[unverified] 경쟁사가 가격을 30% 인하했다.",
        "confidence": "high",
        "sources": ["https://example.com/source"],
    }]

    claim = build_claim_ledger("run-a", findings, raw)[0]

    assert claim.verification_status == "unverified"
    assert claim.executive_summary_eligible is False


def test_inference_is_typed_and_not_promoted():
    raw = _storage(
        ("https://example.com/source", "High capital expenditure increases fixed costs.")
    )
    findings = [{
        "finding": "[추론] 높은 Capex는 가격 하락기에 고정비 위험을 키울 수 있다.",
        "confidence": "medium",
        "sources": ["https://example.com/source"],
    }]

    claim = build_claim_ledger("run-a", findings, raw)[0]

    assert claim.claim_type == "finvision_interpretation"
    assert claim.executive_summary_eligible is False


def test_only_final_findings_are_present():
    raw = _storage(("https://example.com/source", "Final claim is supported."))
    findings = [{
        "finding": "Final claim is supported.",
        "confidence": "medium",
        "sources": ["https://example.com/source"],
    }]

    ledger = build_claim_ledger("run-a", findings, raw)

    assert len(ledger) == 1
    assert "gross 5.8B" not in ledger[0].claim_text


def test_safe_summary_policy_can_only_select_eligible_claims():
    raw = _storage(
        ("https://a.example/source", "Revenue was $25 billion."),
        ("https://b.example/source", "Revenue was $25 billion."),
    )
    findings = [
        {
            "finding": "Revenue was $25 billion.",
            "confidence": "high",
            "sources": ["https://a.example/source"],
        },
        {
            "finding": "[unverified] Competitor cut prices by 30%.",
            "confidence": "high",
            "sources": ["https://a.example/source"],
        },
    ]

    ledger = build_claim_ledger("run-a", findings, raw)
    selected = [c.claim_text for c in ledger if c.executive_summary_eligible]

    assert selected == ["Revenue was $25 billion."]
