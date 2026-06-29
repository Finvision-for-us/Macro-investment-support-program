"""Tavily Search 래퍼 — 얕은 리서치 전용."""
from __future__ import annotations

from dataclasses import dataclass

from tavily import TavilyClient

from src.config import TAVILY_API_KEY
from src.cost_guard import log_call


class MissingTavilyKeyError(RuntimeError):
    """Tavily API 키 미설정 — retry 대상에서 제외."""


@dataclass
class TavilyHit:
    title: str
    url: str
    snippet: str
    score: float = 0.0


def _client() -> TavilyClient:
    if not TAVILY_API_KEY:
        raise MissingTavilyKeyError(
            "TAVILY_API_KEY not set. Get one at https://tavily.com and add it to .env"
        )
    return TavilyClient(api_key=TAVILY_API_KEY)


def search(
    query: str,
    max_results: int = 5,
    include_answer: bool = False,
    days: int | None = 14,
) -> list[TavilyHit]:
    """Tavily Search. 최근 ``days``일 결과로 제한 (default 2주)."""
    client = _client()
    kwargs: dict = {
        "query": query,
        "max_results": max_results,
        "include_answer": include_answer,
        "search_depth": "basic",
    }
    if days is not None:
        kwargs["days"] = days
    resp = client.search(**kwargs)
    log_call("tavily", "search", notes=query[:80])

    hits: list[TavilyHit] = []
    for r in resp.get("results", []):
        hits.append(
            TavilyHit(
                title=r.get("title", "") or "",
                url=r.get("url", "") or "",
                snippet=r.get("content", "") or "",
                score=float(r.get("score", 0.0) or 0.0),
            )
        )
    return hits
