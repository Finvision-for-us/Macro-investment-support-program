"""소스 어댑터 공통 인터페이스.

새 소스 추가 = BaseCollector 하위 클래스 하나 작성 + registry 등록. 파이프라인
본체는 수정 없음. fetch=원본 수집, normalize=공통 스키마 변환의 2단계로,
설계서의 "원본 저장 → 정규화 저장"과 1:1 매칭된다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..schema import NewsItem, RawRecord, TrustTier


class BaseCollector(ABC):
    """모든 소스 어댑터가 따르는 계약."""

    source_id: str          # 하위 클래스에서 지정 ("sec_edgar", "finnhub" ...)
    trust_tier: TrustTier   # 하위 클래스에서 지정

    @abstractmethod
    def fetch(self, since: datetime, until: datetime) -> list[RawRecord]:
        """[since, until) 구간의 원본 레코드를 소스에서 가져온다 (정규화 전)."""
        ...

    @abstractmethod
    def normalize(self, raw: RawRecord) -> NewsItem:
        """원본 1건을 공통 NewsItem으로 변환한다."""
        ...

    @staticmethod
    def make_item_id(source_id: str, source_native_id: str) -> str:
        """재수집 시에도 동일한 결정적 ID. (source_id, source_native_id) 유니크 키."""
        return f"{source_id}:{source_native_id}"
