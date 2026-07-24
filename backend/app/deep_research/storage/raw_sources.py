"""방어선 1: Raw Source Storage — 검색/추출된 원본 텍스트 저장소."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class RawSource:
    url: str
    title: str
    text: str
    domain: str = ""
    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    publisher: str | None = None
    published_at: str | None = None
    document_type: str | None = None
    reporting_period: str | None = None
    source_section: str | None = None
    source_type: str | None = None


class RawSourceStorage:
    """job_id별 원본 텍스트 저장소. pipeline 1회 실행마다 독립 인스턴스."""

    def __init__(self):
        self._store: dict[str, RawSource] = {}  # url → RawSource

    def store(
        self, url: str, title: str, text: str, domain: str = "",
        *, publisher: str | None = None, published_at: str | None = None,
        document_type: str | None = None, reporting_period: str | None = None,
        source_section: str | None = None, source_type: str | None = None,
    ) -> None:
        if url and text:
            self._store[url] = RawSource(
                url=url, title=title, text=text, domain=domain,
                publisher=publisher, published_at=published_at,
                document_type=document_type, reporting_period=reporting_period,
                source_section=source_section, source_type=source_type,
            )

    def get(self, url: str) -> RawSource | None:
        return self._store.get(url)

    def all_sources(self) -> list[RawSource]:
        return list(self._store.values())

    def all_texts_combined(self, max_chars: int = 200_000) -> str:
        """검증용 전체 원본 텍스트 합치기."""
        parts = []
        used = 0
        for src in self._store.values():
            chunk = f"[{src.domain or src.url}]\n{src.text[:3000]}\n"
            if used + len(chunk) > max_chars:
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n".join(parts)

    def get_by_domain_priority(self) -> list[RawSource]:
        """신뢰도 높은 도메인 우선 정렬 — source_registry 단일 기준 파생.

        (이전의 로컬 HIGH 집합은 다른 신뢰도 목록 4곳과 어긋나 있었다.)
        """
        from app.deep_research.sources.source_registry import get_domain_tier
        def _score(s: RawSource) -> int:
            tier = get_domain_tier(s.domain)
            if tier in (1, 2): return 0
            if "gov" in s.domain or "edu" in s.domain: return 1
            if tier == 4: return 3
            return 2
        return sorted(self._store.values(), key=_score)

    def __len__(self) -> int:
        return len(self._store)
