# ingest2 빌드 플랜

> 설계서 `DESIGN.md`를 북극성으로, **단계별로 진행**한다. 각 단계는 ①목표 ②산출물 ③필요한 결정 ④검증 게이트를 가진다. 결정이 안 끝나면 다음 단계로 안 넘어간다. 이 문서가 "지금 어디고 다음이 뭔지"의 단일 출처.

## 작업 방식

1. `DESIGN.md`의 파이프라인 단계를 따라간다.
2. 각 단계 진입 시 **필요한 결정**을 먼저 합의한다 (아래 결정 로그에 기록).
3. 결정을 코드로 굳히고 **검증 게이트**를 통과해야 다음 단계로.
4. 설계 변경은 `DESIGN.md`를 갱신하고 결정 로그에 남긴다.

## ingest2 스코프 경계

| 담당 (ingest2) | 담당 안 함 (다운스트림 / 후속) |
|---|---|
| 1.수집 2.원본저장 3.정규화 4.1차필터 | 6.중복제거 7.후보생성 8.AI분석 9.최종편집/랭킹 |
| (5.분류는 경계 — 결정 D6 대기) | UI / 피드백 |

목표: **검증 가능한 독립 수집 파이프라인**을 만들고, 기존 `src/ingest`(polygon)와 출력 비교 후 대체 여부 결정.

## 단계별 로드맵

| 단계 | 목표 | 산출물 | 필요한 결정 | 검증 게이트 | 상태 |
|---|---|---|---|---|---|
| **P0 계약** | 공통 스키마 + 어댑터 인터페이스 | `schema.py`, `collect/base.py` | D1~D5 | import·유효성 통과 | ✅ 완료 |
| **P1 저장 척추** | 2단계 저장 + 오케스트레이터 골격 | `store/raw_store.py`, `store/news_store.py`, `run.py`, `tests/test_store.py` | D7 저장 방식 | 저장·조회·재수집 중복차단 (3/3 통과) | ✅ 완료 |
| **P2 첫 수직 슬라이스** | RSS 어댑터로 실제 데이터가 수집→원본→정규화→저장까지 흐름 | `collect/rss.py`, `tests/test_rss.py`, +feedparser | D8 피드 목록 | 라이브 12건 저장·재수집 중복차단 (테스트 5/5) | ✅ 완료 |
| **P3 1차 필터** | 명백 탈락 제거 + 사유 기록 (파이프라인 형태 완성) | `filter/basic.py`, `tests/test_filter.py`, schema +flags | D9 임계값 | 라이브 61건→58통과/3탈락(too_old), SEC 49/49 면제 (테스트 16/16) | ✅ 완료 |
| **P3.5 경량 분류** | companies·직접티커·이벤트 부착 (결정론, LLM 없음) | `classify/{tickers,events,basic}.py`, `tests/test_classify.py`, schema +source_meta | D6 위치/범위 | SEC CIK→티커, RSS 고정밀, Polygon 제공 티커 보존. 1글자 티커 오탐 방지(Mavenir→M 제거) | ✅ 완료 |
| **P3.6 깊은 분류(Gemini)** | 간접티커(파급종목)·long-tail·이벤트정제·관련성 | `llm.py`, `classify/deep.py`, `tests/test_deep.py` | D13 Gemini 패턴 | 라이브: GOOGL→간접 MSFT·META·AMZN·NVDA 등, 이벤트 정제 (테스트 27/27) | ✅ 완료 |
| **§6 중복 제거(신규)** | 같은 사건을 EventCluster로 묶음 (정확+구조+어휘+임베딩) | `schema.py +EventCluster`, `dedup/{cluster,embed}.py`, `tests/test_dedup.py` | D14 가/나, D15 임베딩 | 테스트 36/36. **라이브 실병합 1건**(Dow 장마감 시황 2건을 의미로 병합, 티커 없이) | ✅ 완료 |
| **§7 후보 생성+리서치(신규)** | EventCluster → 시그널/스토리 후보 한 바구니 (어댑터로 src/causal·src/research 재사용) | `candidates/{adapter,prescore,pipeline}.py`, `tests/test_candidates.py` | D16 재사용/흐름/리서치범위 | 테스트 13/13 (전체 49/49). 라이브 스모크 ✅ | ✅ |
| **§8 AI 분석층(신규)** | prescore 임시값 → Gemini 정밀 영향도(impact·direction·confidence) | `analyze/{__init__,score}.py`, `tests/test_analyze.py` | D17 혼합 Top-K | 테스트 18/18. 라이브: shallow 10, deep 2, §8 scoring 정상 | ✅ 완료 |
| **§9 최종 랭킹(신규)** | §8 후보 → 노출용 Top-N 편집 규칙 | `rank/final.py`, `tests/test_final_rank.py` | D18 편집 규칙 | 테스트 4/4. 라이브: 10 scored → 5 final, 법률광고·무티커·중복 과점 완화 | ✅ 완료 |
| **P4 소스 확장** | SEC EDGAR / Polygon 뉴스 API | `collect/sec_edgar.py`, `collect/polygon_news.py`, `collect/registry.py` | D10 Polygon, D12 SEC 폼타입 | 라이브: Polygon 168건 수집·중복차단, 전체 수집 179건. 테스트 5/5(SEC+Polygon) | ✅ 완료 |
| **P5 시장데이터 보강** | 가격 반응 enrichment | `MarketSnapshot`, `enrich/market.py` | D11 데이터 소스 | 뉴스에 가격반응 부착 확인 | ⬜ 보류(yfinance 제외) |
| **P6 검증·비교** | 기존 polygon 파이프라인 vs ingest2 출력 비교 | `tests/compare_pipelines.py` | — | 기사수/중복률/필드충족/비용 비교표 → 대체 결정 | ⬜ |

## 결정 로그 (확정)

- **D17 Top-K 선정 방식** = **혼합(A)**. 시그널·스토리 동일 풀에서 impact_score 내림차순 Top-K. 슬롯 배정 없음 — 그날 뉴스에 따라 구성이 달라지는 것이 정직하고, §8에서 스토리는 자연히 높은 impact를 받도록 설계됨 (2026-06-29)
- **D18 §9 최종 랭킹 규칙** = §8 impact를 기본값으로 하되, 다중 이벤트 스토리·deep research·출처 다양성은 보너스, 티커 없음·법률광고성 class-action 알림은 패널티. 동일 primary ticker 과점과 법률광고 후보 수를 cap으로 제한한다. 억지로 10개를 채우지 않고 `min_final_score`를 통과한 후보만 노출한다. (2026-06-29)
- **D10 뉴스 API** = **Polygon.io ticker news**부터 시작. `POLYGON_API_KEY` 보유, `PolygonNewsCollector`를 `BaseCollector` 계약으로 추가하고 registry 기본 수집기에 포함. `source_id=polygon_news`, `trust_tier=2`, API 제공 tickers를 직접티커 초기값으로 사용하되 경량 분류에서 오탐 보정. (2026-06-29)

- **D1 시장 범위** = 미국 단독 (2026-06-22)
- **D2 소스** = SEC·뉴스API·RSS·시장데이터. 텔레그램·크롤링 제외 (2026-06-22)
- **D3 아키텍처** = 공통 스키마 + 어댑터(`BaseCollector`: fetch/normalize 2단계) (2026-06-22)
- **D4 스키마** = 직접/간접 티커 분리, `event_type` 통제어휘, price_up/down 분리, filter_status+rejected_reasons, 재수집키 `(source_id, source_native_id)` (2026-06-22)
- **D5 스타일** = Pydantic v2 + `from __future__ import annotations`, ruff 100, 패키지 내부 상대 import (2026-06-22)
- **D7 저장 방식** = (A) 원본 JSONL(append, `data/ingest2/raw/{source}/{date}.jsonl`) + 정규화 SQLite(`data/ingest2/news.db`, item_id PK→INSERT OR IGNORE 중복차단) (2026-06-22)
- **D8 시작 RSS 피드** = `rss_cnbc_finance`(CNBC Finance, id 10000664) + `rss_marketwatch_bulletins`(MW Bulletins). ※실측: CNBC엔 "Markets" 단일피드 없음, MW MarketPulse·일부 피드는 DowJones 이전으로 ~1년 동결됨 → Bulletins가 신선+시장성. 확장 1순위=Google News 쿼리 피드(100건/신선). feedparser 의존성 추가됨 (2026-06-22)
- **D12 SEC 폼타입** = 넓게: 8-K, S-1, 4, SC 13D, 10-Q. getcurrent Atom 피드, UA 필수(env SEC_USER_AGENT), type 접두어 과매칭은 카테고리 정확매칭으로 제거(type=4→425 거름), "SC 13D" URL 인코딩. CIK→ticker 매핑은 보류(분류 단계에서). (2026-06-22)
- **(보류) yfinance/시장데이터** = 이번에 제외. 종목별 가격반응은 티커추출(분류) 이후라야 의미 → P5로 미룸 (2026-06-22)
- **D9 1차 필터** = 컷오프 24h / 발행시간없음=통과+플래그(no_timestamp) / 미국시장 관련성 필터 생략. 신뢰도 등급 인지(tier-1 SEC는 recency만; tier 2~3은 off_topic·spam도). 내용없음(`empty`: 제목·요약 모두 빈 것)은 전 tier 탈락. ※`too_short`(글자수 컷오프)는 짧은 핵심 헤드라인 오탐 위험으로 `empty`로 교체. 스키마에 `flags` 추가 (2026-06-22)
- **D6 경량 분류** = ingest2 안의 한 단계(`classify/`, 필터 다음). 이번 패스 100% 결정론(LLM 없음): SEC=CIK→티커(정확), RSS=고정밀만($/()심볼 + 대형주 별칭 사전), 이벤트=키워드. **간접 티커·RSS long-tail은 정밀도 위해 보류 → 향후 Gemini "깊은 분류"**. 스키마에 `source_meta`(SEC cik) 추가. ※초기 '첫토큰' 휴리스틱은 'Federal Reserve'→RSRV 오탐으로 폐기 (2026-06-22)
- **D13 깊은 분류(Gemini)** = `classify/deep.py` + 독립 `ingest2/llm.py`(src/llm 패턴 미러, .env GEMINI_API_KEY, 모델 gemini-3.1-flash-lite). 구조화 출력(response_schema=DeepClassification)으로 간접티커·long-tail직접·event정제·us_market_relevant. 결정론 직접티커 보존하고 보강만. filter 통과분에만, limit/only_missing으로 비용제어. llm 콜러블 주입으로 테스트는 네트워크 없음 (2026-06-22)
- **D14 중복제거** = **(가) ingest2에 신규**. `dedup/cluster.py`: ①정확(url/정규화제목) ②구조블록(공유티커+48h시간창) ③어휘(Jaccard≥0.5) ④임베딩(코사인) → Union-Find 전이병합 → `EventCluster`. **SEC(tier1)는 제목 보일러플레이트라 제목/어휘/임베딩 병합 제외 → 각 공시 독립**. 대표=최상신뢰도→최조기 (2026-06-22)
- **D15 dedup 임베딩(f)** = `dedup/embed.py` gemini-embedding-001(768d), 주입식(embedder=None이면 결정론 폴백, 테스트는 fake embedder로 오프라인). tier≥2만 임베딩(SEC 제외, 비용↓). **임계값 cos 0.80** — 라이브 캘리브레이션: 같은 장마감 시황쌍이 cos 0.82여서 초기 0.85는 놓침(src/cluster도 0.82). 0.80으로 실병합 1건 확인. ※소스 적어 묶을 중복 자체가 드뭄 → 뉴스 API 확장 시 실효↑ (2026-06-22)
- **D16 §7 후보 생성** = **재사용+어댑터**. `candidates/`가 ingest2↔src 다리: `adapter.py`(EventCluster→`src.ingest.Event`, 간접티커 포함), `prescore.py`(값싼 사전점수→Top-K), `pipeline.py`(오케스트레이션). **흐름**: 사전점수 Top-K → pairwise edge → 컴포넌트 분리(시그널/스토리) → **리서치층** → claim기반 edge 재발굴(`infer_from_claims`) → 재그룹 → 스토리 스켈레톤 → 내러티브. 핵심: **리서치는 장식이 아니라 그룹을 바꾼다**(deep claim → 신규 edge → 시그널이 스토리로 승격 가능). **리서치 범위**: 얕은(Tavily)=Top-K 전반, 깊은(Parallel)=스토리+고가치 시그널top-N, `max_deep` 캡 + `cost_guard.should_skip_deep` 연동. 시그널·스토리는 `extract_components` 산출(단일/다중)이 곧 한 바구니 → §8/§9가 공통 스코어·랭킹. 모든 외부호출 주입식(오프라인 테스트). 임시 impact=사전점수(§8서 정밀화) (2026-06-29)

## 미결 결정 (Pending)

- **D11 시장데이터 소스** — yfinance vs polygon (보류)

## 현재 위치

✅ **수집~중복제거(DESIGN 1~6) + §7 후보생성 + §8 AI분석층 + §9 최종랭킹 완성**: ingest2 테스트 **77/77**, ruff 통과.
- §7~§8 라이브 스모크 ✅: Polygon 포함 179건 수집(168 Polygon), clusters 175, shallow 10, deep 2, §8 scoring 10.
- §9 라이브 스모크 ✅: 10 scored → 5 final. Mavenir→M 1글자 티커 오탐 제거, class-action 알림은 패널티 적용.
- `analyze/score.py`: Gemini structured output으로 impact·direction·confidence 재계산, 오류 시 원본 보존, 내림차순 정렬.
- `rank/final.py`: story/deep/source diversity 보너스, no-ticker/legal-solicitation 패널티, primary ticker 다양성 cap.
- **D17 혼합 Top-K** 확정: 시그널/스토리 동일 풀, impact_score 내림차순

➡ 다음 갈림길(택1):
  - **(b) 기존 파이프라인 비교**
  - **(P5) 시장데이터 보강** — 가격 반응/거래량 신호를 §9 final_score에 연결
  - **(품질 튜닝)** Polygon 법률/PR 알림 필터와 티커 매핑 샘플 검수 자동 리포트
