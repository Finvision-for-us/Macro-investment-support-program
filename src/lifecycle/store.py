"""M4 Day 1~2: 일자별 스토리 스냅샷 저장/로드 (PROJECT_SPEC §12.3, §12.4).

매 batch 실행 후 오늘의 스토리를 ``data/lifecycle/YYYY-MM-DD.json`` 으로 저장한다.
다음날 ``link.py`` 가 어제 스냅샷과 오늘 스토리를 매칭해 lifecycle 상태를
부여한다.

스키마는 §12.4 참고. ``Story`` (M2 산출물) → ``LifecycleStory`` 변환은
``from_story`` 헬퍼로.
"""
from __future__ import annotations

import json
from datetime import date as date_t, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.causal.schema import RippleEffect, Story
from src.config import ROOT
from src.macro.fred import MacroEvent
from src.macro.themes import Theme
from src.macro.fred import MacroEvent
from src.macro.themes import Theme

# 테스트가 monkeypatch 로 덮어쓸 수 있도록 module-level 변수
LIFECYCLE_DIR: Path = ROOT / "data" / "lifecycle"

LifecycleState = Literal["active", "evolving", "resolved"]
DATE_FMT = "%Y-%m-%d"


class LifecycleStory(BaseModel):
    """Story 에 시간축 메타데이터를 부여한 스냅샷 단위."""

    story_id: str
    title: str
    narrative_short: str = ""
    narrative_long: str = ""  # UI 카드 펼침용 (M4 Day 11~12)
    tickers: list[str] = Field(default_factory=list)
    score: float = 0.0
    event_ids: list[str] = Field(default_factory=list)
    # M3.5: 1·2·3차 파급효과 (narratives 단계 산출). 빈 list 면 UI 펼침 영역 숨김.
    ripple_effects: list[RippleEffect] = Field(default_factory=list)

    # lifecycle 메타
    state: LifecycleState = "active"
    parent_story_id: str | None = None
    similarity: float | None = None
    linked_at: str | None = None
    first_seen_date: str  # YYYY-MM-DD — 이 스토리가 처음 등장한 날
    last_seen_date: str  # YYYY-MM-DD — 가장 최근 신호가 잡힌 날


class Snapshot(BaseModel):
    """일자별 lifecycle 스냅샷 — JSON으로 직렬화돼 디스크에 저장됨."""

    date: str  # YYYY-MM-DD
    generated_at: str  # ISO-8601
    source_narratives: str | None = None  # 어떤 narratives 파일에서 생성됐는지
    stories: list[LifecycleStory] = Field(default_factory=list)
    # M3.5: 거시지표 변화 + 테마. 빈 list 면 UI 에서 패널 숨김.
    macro_events: list[MacroEvent] = Field(default_factory=list)
    themes: list[Theme] = Field(default_factory=list)


def _ensure_dir() -> None:
    LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True)


def _today() -> str:
    return datetime.now(timezone.utc).strftime(DATE_FMT)


def _parse_date(s: str) -> date_t:
    return datetime.strptime(s, DATE_FMT).date()


def snapshot_path(date_str: str) -> Path:
    """``YYYY-MM-DD`` → 해당 일자 스냅샷 JSON 경로."""
    return LIFECYCLE_DIR / f"{date_str}.json"


def from_story(story: Story, on_date: str) -> LifecycleStory:
    """``Story`` (M2 산출물) → 새 ``LifecycleStory``.

    이 시점에는 어제와의 연결 정보가 없으므로 ``state='active'``,
    ``first_seen_date == last_seen_date == on_date`` 로 초기화.
    이후 ``link.py`` 가 parent/similarity/state 를 갱신한다.
    """
    return LifecycleStory(
        story_id=story.id,
        title=story.title,
        narrative_short=story.narrative_short,
        narrative_long=story.narrative_long,
        tickers=list(story.affected_tickers),
        score=float(story.aggregated_impact),
        event_ids=list(story.event_ids),
        ripple_effects=list(story.ripple_effects),
        state="active",
        first_seen_date=on_date,
        last_seen_date=on_date,
    )


def save_snapshot(
    stories: list[LifecycleStory],
    date_str: str | None = None,
    source_narratives: str | None = None,
    macro_events: list[MacroEvent] | None = None,
    themes: list[Theme] | None = None,
) -> Path:
    """주어진 lifecycle stories를 일자별 JSON으로 저장.

    같은 일자에 다시 호출하면 덮어쓴다 (재실행 안전). ``macro_events`` 와
    ``themes`` 는 옵션 — 미지정 시 빈 list 로 저장.
    """
    _ensure_dir()
    date_str = date_str or _today()
    # 형식 검증 — 잘못된 일자 문자열로 파일을 만들어 list가 깨지는 걸 방지
    _parse_date(date_str)
    snap = Snapshot(
        date=date_str,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_narratives=source_narratives,
        stories=stories,
        macro_events=macro_events or [],
        themes=themes or [],
    )
    out = snapshot_path(date_str)
    out.write_text(snap.model_dump_json(indent=2), encoding="utf-8")
    return out


def load_snapshot(date_str: str) -> Snapshot | None:
    """일자별 스냅샷 로드. 파일 없으면 ``None``."""
    p = snapshot_path(date_str)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return Snapshot(**data)


def list_snapshot_dates(days: int | None = None) -> list[str]:
    """존재하는 스냅샷 일자들 (오름차순).

    ``days`` 가 주어지면 오늘 기준 N일 이내 (``today - N`` ≤ d ≤ ``today``) 만.
    """
    if not LIFECYCLE_DIR.exists():
        return []
    dates: list[str] = []
    for p in LIFECYCLE_DIR.glob("*.json"):
        try:
            _parse_date(p.stem)
        except ValueError:
            continue  # 비-일자 파일명은 무시
        dates.append(p.stem)
    dates.sort()
    if days is None:
        return dates
    today_ord = datetime.now(timezone.utc).date().toordinal()
    cutoff = today_ord - days
    return [d for d in dates if _parse_date(d).toordinal() >= cutoff]


def load_previous_snapshot(before_date: str) -> Snapshot | None:
    """``before_date`` 직전의 가장 최근 스냅샷 (어제·그제 등). 없으면 ``None``."""
    _parse_date(before_date)
    earlier = [d for d in list_snapshot_dates() if d < before_date]
    if not earlier:
        return None
    return load_snapshot(earlier[-1])
