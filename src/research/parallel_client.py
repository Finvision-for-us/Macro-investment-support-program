"""Parallel.ai Search + Extract 래퍼 — 깊은 리서치 전용.

같은 ``session_id`` 안에서 search→extract 호출 시 결과 품질이 최적화됨.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from parallel import Parallel

from src.config import PARALLEL_API_KEY
from src.cost_guard import log_call

_CLIENT_MODEL_HINT = "gemini-2.5-flash-lite"


class MissingParallelKeyError(RuntimeError):
    """Parallel API 키 미설정 — retry 대상에서 제외."""


@dataclass
class ParallelHit:
    url: str
    title: str
    excerpts: list[str]  # 검색 결과 또는 추출 본문 조각

    @property
    def joined_text(self) -> str:
        return "\n\n".join(self.excerpts)


def new_session_id(prefix: str = "deep") -> str:
    """깊은 리서치 1건당 발급. search/extract 호출에 동일 ID 사용."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _client() -> Parallel:
    if not PARALLEL_API_KEY:
        raise MissingParallelKeyError(
            "PARALLEL_API_KEY not set. Get one at https://parallel.ai and add it to .env"
        )
    return Parallel(api_key=PARALLEL_API_KEY)


def _coerce_excerpts(raw) -> list[str]:
    """Parallel 응답의 excerpts 필드가 list/str/None 다양함 → list[str]로 표준화."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    return [str(raw)]


def _to_hits(items) -> list[ParallelHit]:
    hits: list[ParallelHit] = []
    for item in items or []:
        hits.append(
            ParallelHit(
                url=getattr(item, "url", "") or "",
                title=getattr(item, "title", "") or "",
                excerpts=_coerce_excerpts(getattr(item, "excerpts", None)),
            )
        )
    return hits


def search(
    queries: list[str],
    objective: str,
    session_id: str,
    mode: str = "basic",
    max_chars_total: int = 4000,
) -> list[ParallelHit]:
    """깊은 리서치의 search 호출. ``mode='basic'`` 디폴트, 부실하면 ``'advanced'`` 재시도."""
    client = _client()
    resp = client.search(
        search_queries=queries,
        objective=objective,
        mode=mode,
        max_chars_total=max_chars_total,
        client_model=_CLIENT_MODEL_HINT,
        session_id=session_id,
    )
    log_call("parallel", f"search:{mode}", notes=f"{len(queries)} queries")
    return _to_hits(getattr(resp, "results", None))


def extract(
    urls: list[str],
    objective: str,
    session_id: str,
    max_chars_total: int = 10000,
    search_queries: list[str] | None = None,
) -> list[ParallelHit]:
    """깊은 리서치의 extract 호출. ``session_id``는 직전 search와 동일해야 품질 최적화."""
    if not urls:
        return []
    client = _client()
    kwargs: dict = {
        "urls": urls[:20],  # API 최대 20개
        "objective": objective,
        "max_chars_total": max_chars_total,
        "client_model": _CLIENT_MODEL_HINT,
        "session_id": session_id,
    }
    if search_queries:
        kwargs["search_queries"] = search_queries
    resp = client.extract(**kwargs)
    log_call("parallel", "extract", notes=f"{len(urls)} urls")
    return _to_hits(getattr(resp, "results", None))
