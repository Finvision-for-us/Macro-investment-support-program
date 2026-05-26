"""방어선 1: Raw Source Storage — 검색/추출된 원본 텍스트 저장소."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RawSource:
    url: str
    title: str
    text: str
    domain: str = ""
    extracted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class RawSourceStorage:
    """job_id별 원본 텍스트 저장소. pipeline 1회 실행마다 독립 인스턴스."""

    def __init__(self):
        self._store: dict[str, RawSource] = {}  # url → RawSource

    def store(self, url: str, title: str, text: str, domain: str = "") -> None:
        if url and text:
            self._store[url] = RawSource(url=url, title=title, text=text, domain=domain)

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
        """신뢰도 높은 도메인 우선 정렬."""
        HIGH = {"sec.gov", "dart.fss.or.kr", "fred.stlouisfed.org",
                "arxiv.org", "reuters.com", "apnews.com"}
        def _score(s: RawSource) -> int:
            if any(h in s.domain for h in HIGH): return 0
            if "gov" in s.domain or "edu" in s.domain: return 1
            return 2
        return sorted(self._store.values(), key=_score)

    def __len__(self) -> int:
        return len(self._store)
