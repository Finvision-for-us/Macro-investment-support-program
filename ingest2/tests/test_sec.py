"""P(breadth) 검증 — SEC EDGAR 정규화 + 카테고리 정확매칭/윈도우 필터 (오프라인)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from ingest2.collect import sec_edgar
from ingest2.collect.sec_edgar import FormSpec, SecEdgarCollector
from ingest2.schema import RawRecord


def _raw(form: str, accession: str = "0001-26-1") -> RawRecord:
    payload = json.dumps(
        {
            "accession": accession,
            "form": form,
            "company": "Acme Corp",
            "cik": "0000012345",
            "title": f"{form} - Acme Corp (0000012345) (Filer)",
            "link": "https://www.sec.gov/x",
            "summary": "s",
            "updated": "2026-06-22T10:00:00+00:00",
        }
    )
    return RawRecord(
        source_id="sec_edgar",
        source_native_id=accession,
        content_type="json",
        payload=payload,
        url="https://www.sec.gov/x",
        fetched_at=datetime(2026, 6, 22, 11, tzinfo=UTC),
    )


def test_normalize_event_type_mapping():
    col = SecEdgarCollector()
    assert col.normalize(_raw("10-Q")).event_type == "earnings"
    assert col.normalize(_raw("S-1/A")).event_type == "ipo"
    assert col.normalize(_raw("8-K")).event_type == "filing"

    item = col.normalize(_raw("8-K", "acc-9"))
    assert item.item_id == "sec_edgar:acc-9"
    assert item.trust_tier == 1
    assert item.source_name == "SEC EDGAR"
    assert item.companies == ["Acme Corp"]
    assert item.raw_category == "8-K"


def test_formspec_exact_rejects_overmatch():
    spec = FormSpec("4", exact=frozenset({"4", "4/A"}))
    assert spec.accepts("4") and spec.accepts("4/A")
    assert not spec.accepts("425")          # 접두어 과매칭 제거
    assert not spec.accepts("424B5")


def test_fetch_filters_category_and_window(monkeypatch):
    entries = [
        {"id": "urn:accession-number=keep", "title": "4 - Acme (0000012345) (Reporting)",
         "link": "http://x/keep", "tags": [{"term": "4"}],
         "updated_parsed": (2026, 6, 22, 10, 0, 0, 0, 0, 0)},
        {"id": "urn:accession-number=drop425", "title": "425 - Acme (0000012345) (Filer)",
         "link": "http://x/425", "tags": [{"term": "425"}],
         "updated_parsed": (2026, 6, 22, 10, 0, 0, 0, 0, 0)},
        {"id": "urn:accession-number=old", "title": "4 - Acme (0000012345) (Reporting)",
         "link": "http://x/old", "tags": [{"term": "4"}],
         "updated_parsed": (2026, 6, 1, 10, 0, 0, 0, 0, 0)},
    ]
    monkeypatch.setattr(sec_edgar.feedparser, "parse",
                        lambda url, agent=None: type("D", (), {"entries": entries}))
    col = SecEdgarCollector(forms=[FormSpec("4", exact=frozenset({"4", "4/A"}))])
    since = datetime(2026, 6, 22, 0, tzinfo=UTC)
    until = datetime(2026, 6, 23, 0, tzinfo=UTC)
    raws = col.fetch(since, until)
    assert [r.source_native_id for r in raws] == ["keep"]   # 425 제거, 구건 제거
