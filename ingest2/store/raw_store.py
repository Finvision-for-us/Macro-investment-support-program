"""원본 저장 — append-only JSONL.

경로: {base_dir}/{source_id}/{YYYY-MM-DD}.jsonl (fetched_at 기준).
박스째 보관이 목적이라 덮어쓰지 않고 항상 append 한다.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..schema import RawRecord

DEFAULT_RAW_DIR = Path("data/ingest2/raw")


class RawStore:
    def __init__(self, base_dir: Path | str = DEFAULT_RAW_DIR) -> None:
        self.base_dir = Path(base_dir)

    def _path(self, raw: RawRecord) -> Path:
        date = raw.fetched_at.strftime("%Y-%m-%d")
        return self.base_dir / raw.source_id / f"{date}.jsonl"

    def save(self, raw: RawRecord) -> Path:
        """원본 1건을 해당 소스/날짜 파일에 append. 저장된 파일 경로 반환."""
        path = self._path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(raw.model_dump_json() + "\n")
        return path

    def save_many(self, raws: Iterable[RawRecord]) -> list[Path]:
        return [self.save(r) for r in raws]
