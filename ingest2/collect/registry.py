"""활성 수집기 등록. 오케스트레이터는 여기서 받은 수집기들을 그대로 돈다."""
from __future__ import annotations

from .base import BaseCollector
from .polygon_news import PolygonNewsCollector
from .rss import default_collectors as _rss_collectors
from .sec_edgar import SecEdgarCollector


def all_collectors() -> list[BaseCollector]:
    return [*_rss_collectors(), SecEdgarCollector(), PolygonNewsCollector()]
