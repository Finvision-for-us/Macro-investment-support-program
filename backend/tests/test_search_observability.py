import asyncio

from app.deep_research.agents.searcher import Searcher
from app.deep_research.models import DeepResearchRequest, SearchResult
from app.deep_research.pipeline import _build_counter_queries


class _Source:
    def __init__(self, outcome, available=True):
        self.outcome = outcome
        self.available = available

    def is_available(self):
        return self.available

    async def search(self, query):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _run(coro):
    return asyncio.run(coro)


def test_search_attempt_distinguishes_success_and_no_results():
    searcher = Searcher()
    searcher._sources = {
        "ok": _Source([SearchResult(
            url="https://example.com/a", title="a", content="a",
            source_type="ok", relevance_score=1.0,
        )]),
        "empty": _Source([]),
    }

    _run(searcher._search_source("query", "ok"))
    _run(searcher._search_source("query", "empty"))

    assert [a.status for a in searcher.attempts] == ["success", "no_results"]
    assert searcher.attempts[0].result_count == 1


def test_search_attempt_classifies_failures():
    cases = [
        ("timeout", asyncio.TimeoutError(), "timeout"),
        ("denied", PermissionError("forbidden"), "access_denied"),
        ("parse", ValueError("bad json"), "parse_failed"),
        ("provider", RuntimeError("quota exhausted"), "provider_error"),
    ]
    searcher = Searcher()
    searcher._sources = {name: _Source(error) for name, error, _ in cases}

    for name, _, _ in cases:
        assert _run(searcher._search_source("query", name)) == []

    assert [a.status for a in searcher.attempts] == [expected for _, _, expected in cases]


def test_reset_prevents_attempts_leaking_between_runs():
    searcher = Searcher()
    searcher._sources = {"empty": _Source([])}
    _run(searcher._search_source("first run", "empty"))
    assert len(searcher.attempts) == 1

    searcher.reset()

    assert searcher.attempts == []


def test_unavailable_requested_source_is_not_searched():
    from app.deep_research.models import SubQuery

    searcher = Searcher()
    searcher._sources = {
        "disabled": _Source([], available=False),
        "ok": _Source([]),
    }
    _run(searcher.search_queries([
        SubQuery(query="q", sources=["disabled", "ok"]),
    ]))

    disabled = [a for a in searcher.attempts if a.source == "disabled"]
    assert len(disabled) == 1
    assert disabled[0].status == "not_searched"
    assert disabled[0].error_type == "source_unavailable"


def test_counter_queries_are_generic_and_ticker_anchored():
    request = DeepResearchRequest(
        query="이 회사의 성장 논리를 평가해줘",
        context={"ticker": "ACME", "company_name": "Acme Corp"},
    )
    queries = _build_counter_queries(request)

    assert len(queries) == 2
    assert all("ACME" in q.query for q in queries)
    assert all("counter_evidence" in q.rationale for q in queries)
    assert any("site:sec.gov" in q.query for q in queries)
    assert not any(term in " ".join(q.query for q in queries).lower()
                   for term in ("dram", "semiconductor", "micron"))
