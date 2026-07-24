from app.deep_research.models import ExtractedContent, SearchResult, SourceInfo
from app.deep_research.storage.raw_sources import RawSourceStorage


def test_source_models_preserve_reverification_metadata():
    result = SearchResult(
        url="https://example.com/filing",
        title="Annual report",
        content="evidence",
        source_type="official",
        publisher="Example Corp",
        published_date="2026-06-30",
        document_type="10-K",
        reporting_period="FY2026",
        source_section="Risk Factors",
    )
    extracted = ExtractedContent(
        url=result.url,
        title=result.title,
        content="evidence " * 100,
        domain="example.com",
        word_count=100,
        publisher=result.publisher,
        published_at=result.published_date,
        document_type=result.document_type,
        reporting_period=result.reporting_period,
        source_section=result.source_section,
        source_type=result.source_type,
    )
    exported = SourceInfo(
        url=extracted.url,
        title=extracted.title,
        domain=extracted.domain,
        publisher=extracted.publisher,
        published_at=extracted.published_at,
        document_type=extracted.document_type,
        reporting_period=extracted.reporting_period,
        source_section=extracted.source_section,
        source_type=extracted.source_type,
    )

    assert exported.model_dump()["publisher"] == "Example Corp"
    assert exported.model_dump()["source_section"] == "Risk Factors"


def test_raw_source_storage_keeps_metadata_with_evidence():
    storage = RawSourceStorage()
    storage.store(
        "https://example.com/filing",
        "Annual report",
        "material evidence",
        "example.com",
        publisher="Example Corp",
        published_at="2026-06-30",
        document_type="10-K",
        reporting_period="FY2026",
        source_section="Risk Factors",
        source_type="official",
    )

    source = storage.get("https://example.com/filing")
    assert source is not None
    assert source.publisher == "Example Corp"
    assert source.reporting_period == "FY2026"
    assert source.source_section == "Risk Factors"
