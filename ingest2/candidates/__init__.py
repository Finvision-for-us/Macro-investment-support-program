"""§7 후보 생성 + 리서치 — 중복제거 산출물(EventCluster)을 시그널/스토리 후보로.

흐름: EventCluster → Event 어댑터 → 사전점수 Top-K → 1차 인과 edge →
컴포넌트 분리(시그널/스토리) → 리서치(얕은 전반·깊은 스토리+고가치 시그널) →
claim 기반 edge 재발굴 → 재그룹 → 스토리 스켈레톤 → 내러티브.

src/causal · src/research 코드를 그대로 재사용하고, 이 패키지는 ingest2 ↔ src
사이의 어댑터 + 오케스트레이션만 담당한다(합류 시 src 본류로 흡수 검토).
"""
