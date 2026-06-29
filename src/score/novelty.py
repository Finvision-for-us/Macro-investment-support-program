"""Novelty 신호: 최근 N일 내 유사 사건 빈도의 역수.

목적: 흔한 사건(매주 반복되는 NVDA 일반 분석 등)을 디스카운트하고
"이번 주에 새로 등장한" 사건을 부각.

구현:
1. 최근 N일 events JSON 파일 스캔 (현재 파이프라인이 자동 저장한 산출물)
2. 각 historical event 텍스트를 임베딩
3. 현재 event ↔ historical 코사인 유사도 ≥ 임계값이면 "같은 종류 사건"
4. count → novelty = 1 / (1 + log(1 + count))  (count=0 → 1.0, ∞ → 0)
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from src.cluster.embed import embed_texts
from src.config import OUTPUTS_DIR
from src.ingest.schema import Event

NOVELTY_LOOKBACK_DAYS = 30
NOVELTY_SIM_THRESHOLD = 0.70  # 0.70+ = 같은 종류 사건

# 파일명에서 YYYYMMDD_HHMMSS 패턴 추출
_TS_RE = re.compile(r"_(\d{8})_(\d{6})\.json$")


def _event_text(event: Event) -> str:
    return f"{event.title}\n\n{event.summary[:500]}"


def _file_timestamp(path: Path) -> datetime | None:
    m = _TS_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.strptime(
            m.group(1) + m.group(2), "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_historical_events(
    ticker: str,
    lookback_days: int = NOVELTY_LOOKBACK_DAYS,
    exclude_path: Path | None = None,
    base_dir: Path = OUTPUTS_DIR,
) -> list[Event]:
    """최근 N일치 historical events 로드 (현재 파일 제외)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    historical: list[Event] = []

    for f in sorted(base_dir.glob(f"{ticker}_events_*.json")):
        if exclude_path and f.resolve() == exclude_path.resolve():
            continue
        ts = _file_timestamp(f)
        if ts is None or ts < cutoff:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            historical.extend(Event(**e) for e in data)
        except Exception:  # noqa: BLE001
            continue

    return historical


def compute_novelty(
    current_events: list[Event],
    current_embeddings: np.ndarray,
    historical_events: list[Event],
    historical_embeddings: np.ndarray | None = None,
    sim_threshold: float = NOVELTY_SIM_THRESHOLD,
) -> dict[str, float]:
    """각 current event id → novelty 점수 (0~1, 큰 값 = 더 희소).

    historical_embeddings가 없으면 자동 계산. 호출자가 캐시하면 비용 절감.
    """
    if not current_events:
        return {}
    if not historical_events:
        return {ev.id: 1.0 for ev in current_events}

    if historical_embeddings is None or historical_embeddings.size == 0:
        historical_embeddings = embed_texts([_event_text(e) for e in historical_events])

    sim = cosine_similarity(current_embeddings, historical_embeddings)

    novelty: dict[str, float] = {}
    for i, ev in enumerate(current_events):
        similar_count = int(np.sum(sim[i] >= sim_threshold))
        # 1 / (1 + log(1 + count))  → count=0:1.0, 1:0.59, 10:0.29
        novelty[ev.id] = 1.0 / (1.0 + math.log1p(similar_count))

    return novelty
