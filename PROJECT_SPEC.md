# finvision 프로젝트 사양서

> **상태**: M3 Day 1~13 코드 완료. Day 14 사용자 평가 단계.
> **최종 갱신**: 2026-05-25

---

## 0. 한 줄 요약

주가에 영향을 미칠 만한 한국·미국 시장의 뉴스들을 AI 에이전트가 자동 수집하고, **인과 체인**으로 엮어 매일 영향력 상위 **Top 10 스토리**를 제공하는 프로그램.

---

## 1. 비전과 목표

- **궁극 목표**: 사용자가 매일 아침 10분 안에 "오늘 시장에서 무슨 일이 왜 일어나는가"를 인과적으로 이해할 수 있게 한다.
- **차별점**: 단순 뉴스 요약이 아닌, 여러 사건을 인과 관계로 엮은 **스토리**를 생성.
- **신뢰성 원칙**: 모든 주장은 출처를 가진다. 출처 없는 추론은 금지.

---

## 2. 사용자

| 단계 | 사용자 | 시점 |
|---|---|---|
| Phase 1 | 본인 + 소수 지인/팀 | MVP (M1~M5) |
| Phase 2 | 일반 사용자 대상 서비스/제품 | M5 이후 |

---

## 3. 핵심 기능 (합의된 요구사항)

### 3.1 산출물
- 매일 갱신되는 **Top 10 스토리** (한국·미국 시장 뒤섞어 영향력 순)
- 각 스토리는 **인과 체인** 형태 (중간~긴 체인, 노드 4~7개 기대)
- 각 스토리는 **방향성** 라벨 부여 (호재 / 악재 / 불확실)
- 각 인과 edge에는 **신뢰도 점수** (0.0~1.0)
- 모든 주장(claim)에 **출처 1개 이상 인용** 필수
- 출처 1개뿐인 주장은 **"단일 출처" 경고** 표시

### 3.2 갱신 모델
- **데일리 브리핑**: 아침 1회, 전일~당일 개장 전 Top 10 생성
- **라이브 스토리**: 새 이벤트 발생 시 기존 스토리에 합쳐지거나 분기
- **스토리 생명주기**: `Active → Evolving → Resolved`
  - 7일간 신규 이벤트 없거나 주가 변동 안정화 시 Resolved

### 3.3 수집 대상 (모두 포함)
- 기업 공시 (실적, M&A, 자사주 등)
- 일반 뉴스 기사
- 거시경제 지표 (금리, CPI, 환율 등)
- 산업/경쟁사 뉴스
- 애널리스트 리포트
- SNS/커뮤니티 (X, 레딧 등) — *MVP에서 제외, 후순위*
- 정부 정책/규제

### 3.4 종목 매핑
- **종목 단위** + **섹터/테마 단위** 둘 다
- 종목 마스터:
  - 한국: KRX 상장사 전체
  - 미국: NASDAQ + NYSE 전체

---

## 4. 핵심 설계 결정

### 4.1 영향력 점수 공식

```
Impact Score = 0.35 × Spread + 0.30 × MarketCap + 0.20 × Novelty + 0.15 × PriceReaction
```

| 신호 | 정의 | 데이터 출처 |
|---|---|---|
| **Spread (확산도)** | 동일 사건을 다룬 매체 수 × log(보도 빈도) | 임베딩 클러스터링 |
| **MarketCap (시총)** | 영향받는 종목들의 시총 합 (정규화) | KRX, yfinance |
| **Novelty (희소성)** | 최근 30일 같은 유형 사건 빈도의 역수 | 자체 이벤트 DB |
| **PriceReaction (가격반응)** | 사건 후 종목/섹터 단기 수익률 절댓값 | 가격 API |

- **튜닝**: 운영하면서 사용자가 Top 10을 평가 → 회귀로 가중치 자동 조정
- **MVP(M1~M2)**: Spread + MarketCap만 사용 (단순화)
- **M3 이후**: Novelty + PriceReaction 추가

### 4.2 인과 추론 정책
- **자동 추론 + 신뢰도 표시** 방식
- 신뢰도는 **숫자 0.0~1.0**로 노출
- 인과 edge 속성: `confidence`, `direction(+/-)`, `mechanism(설명)`

### 4.3 인용 정책
- 모든 **주장(claim)** 단위로 출처 1개 이상
- 한 문장에 여러 출처 가능
- 출처 1개뿐 → `is_single_source = true` 플래그로 UI에 경고

### 4.4 스토리 표현
- 길이: **요약(300자) + 펼치기(800~1500자)** 둘 다 제공
- 인과 체인 시각화는 M2 이후 고려

---

## 5. 시스템 아키텍처

### 5.1 데이터 흐름

```
[수집]──> [Dedup/Cluster]──> [Entity]──> [Causal Graph]──> [Story Ranker]──> [내러티브]
            │                    │             │                 │              │
            ▼                    ▼             ▼                 ▼              ▼
        ①배경조사            ②관계자맥락    ③원인탐색         ④교차검증     ⑤스토리완성
                            (Deep Research 적용 지점)
```

### 5.2 딥리서치 통합 지점 (5곳)

| # | 위치 | 역할 |
|---|---|---|
| ① | 클러스터 형성 직후 | 배경/선례/이해관계자 능동 검색 |
| ② | Entity Linking 직후 | 종목/인물 최근 동향 보충 |
| ③ | Causal Graph 구축 시 | **DB에 없는 상류 원인 능동 탐색** (핵심) |
| ④ | 인과 edge 신뢰도 평가 시 | **반박 근거 능동 검색** (핵심) |
| ⑤ | Top 10 내러티브 작성 직전 | 빈 구간(gap) 채움 |

### 5.3 딥리서치 전략: **하이브리드 (c)**
- 1차: 모든 클러스터에 **얕은 딥리서치** (Search 1회 + LLM 1회 요약)
- 2차: 영향력 상위 10~20개에만 **깊은 딥리서치** (Plan 1회 → Search × 5 → Read × 10~15 → Synthesize 1회)
- 비용/효과 균형, 영향력 점수 시스템과 연동

### 5.3.1 검색·추출 아키텍처 (패턴 A 정제: 깊이별 분리)
원래 "기능별 분리(Tavily=검색 / Parallel=추출)" 가 합의였으나, 검토 결과 Parallel이 search+extract 모두 제공하며 `session_id`로 묶어야 품질 최적화됨을 발견 → **"깊이별 분리"** 로 재설계:

| 단계 | 사용 도구 | 이유 |
|---|---|---|
| **얕은 리서치 (모든 클러스터, 50+)** | **Tavily Search 단독** | 무료 1000/월, 빠름, 깊이 불필요 |
| **깊은 리서치 (Top 5~10)** | **Parallel Search + Extract (동일 `session_id`)** | session 안에서 search↔extract가 서로 참조 → excerpt 품질 ↑ |
| **추론** | **Gemini Flash Lite** (Plan + Synthesize) | 검색·추출과 분리, LLM은 텍스트만 |

**효율 트릭** (구현 시 적용):
- Parallel `mode="basic"` 디폴트, 결과 부실 시 `"advanced"` 재시도 (비용 ~1/3)
- `client_model="gemini-2.5-flash-lite"` 파라미터로 Parallel에 알려줌 → 응답 포맷 최적화
- Plan 단계 sub-question 3~4개로 제한 (기존 5개)
- 추출 본문 길이 제한 (`max_chars_total=10000`)
- 얕은 리서치 결과 cache

**왜 Gemini grounding 안 씀**:
- redirect URL 단기 만료 (인용 검증 불가)
- 무료 tier 한도 빡빡 (오늘 NVDA 1회 테스트하다 소진)

### 5.4 스토리 생명주기

```
신규 이벤트 ──┬──> 기존 스토리에 매칭? ──Yes──> Evolving (스토리 갱신, 알림)
              │
              └──> 매칭 없음 ──> 신규 스토리 생성 → Active
                                                      │
                                                      ▼ (7일 무변동 or 주가 안정화)
                                                  Resolved
```

---

## 6. 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| **언어** | Python | LLM/데이터 생태계 |
| **LLM (텍스트)** | Gemini Flash Lite (2.5/3.1) | 무료 tier 사용 가능, 추론만 담당 |
| **검색 API** | **Tavily Search API** | AI agent 친화적, 무료 1000/월 |
| **본문 추출 API** | **Parallel Web Reader API** | 깔끔한 추출, 무료 크레딧 ~$5 |
| **임베딩** | Gemini `gemini-embedding-001` (768d Matryoshka) | 다국어 지원, 한국 시장 확장 대비 |
| **저장소 (MVP)** | SQLite | 단순 시작 |
| **저장소 (스케일)** | PostgreSQL + pgvector | M3 이후 |
| **뉴스 수집 (미국)** | Polygon.io, Finnhub, SEC EDGAR | 티커별 뉴스, 공시 |
| **뉴스 수집 (한국)** | 네이버 뉴스 API, DART 공시, 한경/매경 RSS | 무료 + 안정 |
| **거시 데이터** | FRED API (미), ECOS API (한) | 무료, 공식 |
| **시가총액** | yfinance, KRX | 무료 |
| **스케줄링 (MVP)** | cron | 단순 |
| **스케줄링 (스케일)** | Celery 또는 Temporal | M5 이후 |
| **프론트엔드** | Next.js | M4부터 |

---

## 7. 데이터 모델 (핵심 테이블)

```sql
-- 원자 단위 사건 (뉴스 클러스터 1개)
events (
  id, title, summary, occurred_at, source_urls[], publishers[],
  embedding, entities[], event_type, tickers_mentioned[], spread
)

-- 이벤트 간 인과 관계
causal_edges (
  from_event_id, to_event_id, confidence, direction, mechanism, created_by
)

-- 인과 그래프의 부분그래프
stories (
  id, title, narrative_short, narrative_long, status,
  impact_score, direction, affected_tickers[],
  created_at, last_updated_at
)

-- 스토리-이벤트 연결
story_events (story_id, event_id, position_in_chain)

-- 문장 단위 출처
citations (story_id, sentence_idx, source_url, quote, is_single_source)

-- 비용 추적
api_calls (timestamp, provider, endpoint, tokens_in, tokens_out, cost_usd)
```

---

## 8. 로드맵

| 단계 | 기간 | 산출물 | 검증 기준 |
|---|---|---|---|
| **M1** | 2주 | Ingestion + Dedup + Entity Linking + 얕은/깊은 딥리서치. NVDA 1종목 CLI 출력 | 본인이 직접 읽는 것보다 빠르고 깊은가 |
| **M2** | 2주 | Causal Graph 구축 + Story 생성. 1종목 1스토리, 내러티브+인용 | 본인이 읽고 "납득 가능" |
| **M3** | 2주 | 영향력 점수 (4신호 전체) + Top 10 랭킹. 미국 전체 일배치 | 본인 평가 Top 10과 50%+ 겹침 |
| **M3.5** | ~1.5주 | **테마 클러스터링 + FRED 거시 + 1·2·3차 파급효과** | "투자 결정에 직접 도움" 자체 평가 |
| **M4** | 2주 | **Story Lifecycle (Active/Evolving/Resolved)** + 미니 웹 UI (`/today`) | 어제 본 스토리가 오늘 자동 갱신/이어짐 |
| **M5** | 2주 | 본격 웹 UI (Next.js) + 실시간 이벤트 갱신 + 푸시 알림 | 팀원 3명 사용 가능 |
| **Phase 2** | 별도 | 한국 시장 (KRX/네이버) + 일반 서비스화 + 다국어 | 외부 사용자 모집 가능 |

**총 ~10주 (M1~M5)**. 각 단계 종료 시 본인 평가 후 다음 단계 진행.
**한국 시장**은 미국에서 파이프라인이 완성되면 Phase 2로 분리 — 데이터 소스/티커 매핑/언어가 다 다르므로 별도 단계.

---

## 9. M1 상세 명세

### 9.1 목표
NVDA 종목 1개로 최근 1주 뉴스 수집·클러스터링 → 영향력 상위 클러스터에 딥리서치 적용 → CLI로 Markdown 리포트 출력.

### 9.2 프로젝트 구조

```
finvision/
├── pyproject.toml
├── .env                          # GEMINI_API_KEY, POLYGON_API_KEY, TAVILY_API_KEY, PARALLEL_API_KEY
├── src/
│   ├── config.py
│   ├── llm.py                    # Gemini 공통 클라이언트/재시도
│   ├── ingest/
│   │   ├── polygon_news.py
│   │   └── schema.py
│   ├── cluster/
│   │   ├── embed.py
│   │   └── cluster.py
│   ├── research/
│   │   ├── schema.py
│   │   ├── tavily_client.py      # Tavily Search 래퍼 (얕은 리서치 전용)
│   │   ├── parallel_client.py    # Parallel Search+Extract 래퍼 (깊은 리서치 전용)
│   │   ├── shallow.py            # Tavily → LLM 요약
│   │   └── deep.py               # Parallel session_id 워크플로 + LLM Plan/Synthesize
│   ├── score/
│   │   └── impact.py
│   ├── storage/
│   │   └── db.py
│   └── cli.py
├── outputs/
└── tests/
```

### 9.3 의존성

```toml
[project.dependencies]
google-genai = ">=1.0"
polygon-api-client = ">=1.14"
tavily-python = ">=0.5"          # 검색 백엔드
parallel-web = ">=0.x"           # Parallel Web Reader (정확한 패키지명 SDK 확인 후)
yfinance = ">=0.2"
numpy = "*"
scikit-learn = "*"
pydantic = ">=2"
python-dotenv = "*"
rich = "*"
tenacity = "*"
```

### 9.4 2주 일정

| Day | 작업 | 검증 |
|---|---|---|
| 1~2 | `ingest/polygon_news.py` — NVDA 1주치 수집 | 50~200건 JSON 덤프 |
| 3~4 | `cluster/embed.py`, `cluster.py` — 임베딩+군집 | 손라벨링 5건과 80%+ 일치 |
| 5~7 | `score/impact.py` — Spread+MarketCap 점수 | 상위 30% 추출 |
| 8~11 | `research/{tavily_client,parallel_client,shallow,deep}.py` — 얕은=Tavily, 깊은=Parallel session, 추론=Gemini | 본인이 모른 새 정보 1개+ 포함 |
| 12~13 | `cli.py` — Markdown 리포트 출력 | 표 9.5 형식 준수 |
| 14 | 평가 | 표 9.6 4개 항목 모두 통과 |

### 9.5 출력 포맷 (Markdown)

```markdown
# NVDA Deep Research Report (YYYY-MM-DD)

## Top N Events (영향력 순)

### 1. [영향력 0.87] 사건 제목
- **방향**: ⚠️ Negative | **신뢰도**: 0.72
- **출처 수**: N개

**배경**
- 항목 [출처]
- ⚠️ 단일출처: 항목 [출처]

**인과 체인**
1. 원인 [출처]
   ↓ (신뢰도 0.85)
2. 결과 [출처]

**반박 근거**
- 항목 [출처]

**Watch Points**
- 항목
```

### 9.6 평가 기준

| 항목 | 기준 | 미달 시 |
|---|---|---|
| 클러스터링 정확도 | 표본 5건 라벨링과 80%+ 일치 | threshold 조정 |
| 딥리서치 깊이 | 본인이 안 찾아본 새 정보 1개+ | 프롬프트 개선, max_iter↑ |
| 인용 정확도 | 표본 10문장 중 8개+ 정확 | 합성 프롬프트에 원문 인용 강제 |
| 비용 | NVDA 1회 실행 ≤ $0.50 | shallow/deep 비율 조정 |

### 9.7 예상 비용 (Tavily + Parallel + Gemini Flash Lite 기준, 깊이별 분리)
- 임베딩 200건 (Gemini): ~$0.01
- Tavily Search 50회 (얕은 리서치 전용): **무료 한도 안** (1000/월)
- Parallel Search 25회 (깊은 5건 × sub-question 5): ~$0.05~0.10 (basic mode)
- Parallel Extract 15회 (깊은 5건 × top URL 3): ~$0.05
- Gemini Plan+Synthesize 10회: ~$0.001
- **합계 ~$0.10~0.20 / 실행** (기존 grounding ~$0.40~0.60 대비 1/3로 절감)
- 일일 운영 (NVDA 1종목 데일리): 월 ~$3~6

---

## 10. M2 상세 명세 (Causal Graph + Story 생성)

### 10.1 목표
M1 코드 위에서 이벤트 간 **인과 관계**를 추론하고, 연결된 이벤트들을 **Story**로 묶어 내러티브를 생성한다.

### 10.2 합의된 설계 결정 (2026-05-18)

| 항목 | 결정 |
|---|---|
| 인과 edge 발견 방법 | **(c) Top 20 이벤트 쌍 LLM 검증** + **(b) deep research의 claim 재활용** |
| Story 크기 | **최소 1 노드** (단일 이벤트도 Story로 인정) ~ **최대 20 노드** |
| 다종목 지원 | **허용** — 그래프는 자연스럽게 멀티티커 가능, UI 필터만 종목별 |

### 10.3 새 모듈 구조

```
src/causal/
├── __init__.py
├── schema.py            # CausalEdge, Story
├── edges.py             # pairwise LLM + claim 기반 edge 추론
├── graph.py             # 그래프 빌드 + 연결 컴포넌트 (Story 후보) 추출
└── story.py             # Story 단위 내러티브 생성
```

### 10.4 데이터 모델 추가

```python
class CausalEdge(BaseModel):
    from_event_id: str
    to_event_id: str
    confidence: float                # 0.0~1.0
    direction: Literal["positive", "negative", "uncertain"]
    mechanism: str                   # 짧은 인과 메커니즘
    source_urls: list[str]           # 근거 출처
    inferred_by: Literal["pairwise_llm", "deep_research_claim"]

class Story(BaseModel):
    id: str
    event_ids: list[str]
    title: str
    narrative_short: str             # ~300자 (카드형)
    narrative_long: str              # 800~1500자 (상세)
    direction: Literal["positive", "negative", "uncertain"]
    confidence: float
    affected_tickers: list[str]
    aggregated_impact: float
    edges: list[CausalEdge]
    all_sources: list[str]
```

### 10.5 새 CLI 서브커맨드

```bash
python -m src.cli edges NVDA       # 인과 edge 추론 → NVDA_edges_*.json
python -m src.cli stories NVDA     # 그래프 + Story 생성 → NVDA_stories_*.json
python -m src.cli report NVDA      # (업데이트) Story 단위 Markdown 출력
```

### 10.6 파일 흐름

```
NVDA_scored_*.json
NVDA_research_*.json (deep+shallow)
   ↓
NVDA_edges_*.json        ← Day 1~3
   ↓
NVDA_stories_*.json      ← Day 4~8
   ↓
NVDA_report_*.md         ← Day 9~10 (Story 단위 갱신)
```

### 10.7 비용 최적화: 사전 필터링

Top 20 이벤트 = C(20,2) = **190쌍**. 전부 LLM 호출 시 비싸므로 다음 중 1개라도 통과한 쌍만 LLM 검증:

| 필터 | 통과 조건 |
|---|---|
| **티커 중복** | ≥1개 종목 공유 |
| **시간 근접** | 14일 이내 발생 |
| **의미 유사도** | 임베딩 코사인 ≥ 0.55 |

일반적으로 30~60쌍만 LLM 호출 통과 → 비용 대폭 절감.

### 10.8 2주 일정

| Day | 작업 | 검증 |
|---|---|---|
| 1~3 | `causal/edges.py` — pairwise LLM + claim 기반 edge 추론 | Top 20 → 5~30개 edge 발견 |
| 4~5 | `causal/graph.py` — 그래프 빌드 + 연결 컴포넌트 | 크기 분포 합리적 (대부분 1~5 노드) |
| 6~8 | `causal/story.py` — 내러티브 생성 (short + long) | 본인이 읽고 "납득 가능" |
| 9~10 | `report/markdown.py` 업데이트 — Story 단위 렌더링 | Top 10 Story 출력 |
| 11~12 | 통합 테스트 + 비용/품질 튜닝 | 풀 파이프라인 ≤ $0.30 |
| 13~14 | 평가 + 스펙 업데이트 | 10.10 평가 기준 통과 |

### 10.9 예상 비용 (NVDA 1회 풀 실행, M1 위에 추가)

- Edges: 30~60 LLM 호출 (Flash Lite) × ~$0.0005 = **~$0.02**
- Story narrative: 10~15 Story × 1 LLM = **~$0.01**
- **M2 추가 비용 ~$0.03~0.05** (M1 포함 총 ~$0.13~0.25)

### 10.10 평가 기준 (Day 14)

| 항목 | 기준 | 미달 시 |
|---|---|---|
| Edge 정밀도 | 본인이 표본 10개 edge 검수 → 7개+ 합리적 | 프롬프트 강화, 필터 조정 |
| Story 일관성 | Top 5 Story 읽고 "내러티브가 말이 됨" | 합성 프롬프트 개선 |
| 인용 정확도 | M1 기준 유지 (10문장 중 8+) | citation guard 추가 |
| 비용 | ≤ $0.30 / 실행 (M1 포함) | 필터 튜닝, 모델 다운그레이드 |

---

## 11. M3 상세 명세 (영향력 4신호 완성 + 다종목 배치)

### 11.1 목표
영향력 점수에 **Novelty + PriceReaction** 신호를 추가해 4신호 완성하고, **30개 시드 종목 (섹터 균형)** 으로 시장 전체 Top 10 스토리를 생성한다.

### 11.2 합의된 설계 결정 (2026-05-24)

| 항목 | 결정 |
|---|---|
| 시드 종목 | **30 (섹터별 균형)** |
| 비용 정책 | **무료 tier 유지 + Parallel 한도 가드** |
| 우선순위 | **Week 1: 신호 (NVDA 검증) → Week 2: 다종목 스케일** |
| 자동화 | 수동 트리거 only (cron 미적용) |

### 11.3 시드 종목 리스트 (섹터 균형 30개)

| 섹터 | 종목 |
|---|---|
| Tech 메가 (7) | NVDA, MSFT, GOOGL, AAPL, META, AMZN, TSLA |
| 반도체 (6) | AVGO, AMD, INTC, MU, QCOM, TSM |
| 금융 (5) | JPM, BAC, V, MA, BRK.B |
| 헬스케어 (4) | LLY, UNH, JNJ, PFE |
| 소비재 (5) | WMT, COST, HD, PG, KO |
| 에너지/산업 (3) | XOM, CVX, BA |

### 11.4 새 모듈

```
src/
├── score/
│   ├── impact.py         # (갱신) 4신호 합산
│   ├── novelty.py        # 신규 — 희소성
│   └── price_reaction.py # 신규 — 가격 반응
├── cost_guard.py         # 신규 — Parallel 크레딧 추적
└── universe/
    ├── __init__.py
    └── seeds.py          # 신규 — 시드 종목 리스트
```

### 11.5 4신호 합산 공식

```
Impact = 0.35 × Spread + 0.30 × MarketCap + 0.20 × Novelty + 0.15 × PriceReaction
```

- **Novelty**: 최근 30일 내 유사 사건 빈도의 역수 → 흔한 사건 디스카운트
- **PriceReaction**: 사건 후 1/3일 영향종목 수익률 절댓값 → 시장 반응 큰 사건 부각

### 11.6 새 CLI 서브커맨드

```bash
python -m src.cli batch --universe top30 [--days 7]   # 30종목 통합 배치
python -m src.cli costs                                # API 사용량 + Parallel 잔여 추정
```

### 11.7 2주 일정

| Week | Day | 작업 | 검증 |
|---|---|---|---|
| 1 | 1~2 | `score/novelty.py` | NVDA Top 10 변화 (희소 사건 부각) |
| 1 | 3~4 | `score/price_reaction.py` | 시장 반응 큰 사건 부각 |
| 1 | 5~7 | 4신호 통합 + NVDA 재실행 | Top 10이 직관적으로 개선 |
| 2 | 8~9 | `universe/seeds.py` + 다종목 ingest | 30 ticker → 통합 events |
| 2 | 10~11 | 다종목 풀 파이프라인 | 시장 단위 Top 10 스토리 |
| 2 | 12~13 | `cost_guard.py` + `batch` CLI | 한도 임박 시 경고/skip 동작 |
| 2 | 14 | 평가 | §11.10 4개 항목 통과 |

### 11.8 비용 가드 동작

- 매 호출마다 `api_calls` SQLite 누적
- Parallel 호출당 추정 비용 ($0.02~$0.05 mode별)
- 잔여 크레딧 < 20% → 콘솔 경고
- 잔여 크레딧 < 5% → deep research 자동 skip, shallow만 사용

### 11.9 예상 비용 (1일 운영, 무료 tier 가정)

- Polygon: 30 ticker × 1 call = 30 (6분, free 5/min OK)
- Tavily: Top 10 shallow = 10/일 → 월 300 (1000/월 안)
- **Parallel: deep Top 3 = 6/일 → ~2주 만에 $5 크레딧 소진**
- Gemini: ~90 calls/일 → 1500 RPD 안

→ 무료로 **약 2주 daily** 가능. 이후 결제 필수 (월 ~$10).

### 11.10 평가 기준 (Day 14)

| 항목 | 기준 | 미달 시 |
|---|---|---|
| Novelty 효과 | 흔한 큰 뉴스 (NVDA 일상 분석)이 Top 10에서 빠짐 | sim_threshold 조정 |
| PriceReaction 효과 | 실제 큰 가격 변동 사건이 Top 10에 진입 | 가중치 튜닝 |
| 다종목 스토리 | 시장 단위 Top 10 중 절반+ 멀티티커 | seed list 보정 |
| 비용 가드 | Parallel 한도 임박 시 정상 동작 | 한도 추정 보정 |

---

## 12. M4 상세 명세 (Story Lifecycle + 미니 UI)

### 12.1 목표
어제 본 스토리가 오늘 자동으로 **이어지거나 / 진화하거나 / 종결**되도록 만들고, 그 상태를 한 페이지 웹 UI에서 확인.

**합의 배경 (2026-05-27):**
- 한국 시장은 데이터 소스·매핑·언어가 모두 다른 별도 프로젝트 → Phase 2로 연기
- M3까지는 "1회 실행" 파이프라인이었음. M4는 **시간축 (어제→오늘)** 을 추가하는 핵심 단계
- Lifecycle만 만들면 효과를 눈으로 확인하기 어려움 → 최소 UI 동반

### 12.2 합의된 설계 결정 (2026-05-27)
- **Lifecycle 상태 3단계**: 🟢 `active` (새로 생김 또는 진화중) / 🟡 `evolving` (어제 본 스토리에 새 이벤트 합류) / ⚫ `resolved` (3일 새 이벤트 없음)
- **연결 기준**: 어제 Top 30 스토리 ↔ 오늘 후보 스토리, 임베딩 유사도 ≥ 0.75 + 공통 ticker ≥ 1
- **UI**: Next.js 14 (App Router) + Tailwind, 단일 페이지 `/today`. 백엔드는 JSON 파일 직접 읽기 (DB 불필요)
- **호스팅 / 인증 / 다중유저**: M4에서 다루지 않음 (M5)

### 12.3 새 모듈 / 파일 구조

```
finvision/
├── src/
│   ├── lifecycle/
│   │   ├── __init__.py
│   │   ├── store.py            # 일자별 스토리 스냅샷 (JSON) 저장/로드
│   │   ├── link.py             # 어제 ↔ 오늘 스토리 매칭 (임베딩+ticker)
│   │   └── state.py            # active/evolving/resolved 판정
│   └── cli.py                  # `lifecycle` 서브커맨드 추가
├── ui/                         # ★ 신규
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx            # 리다이렉트 → /today
│   │   └── today/
│   │       └── page.tsx        # 메인 페이지 (Top 10 + 상태 배지)
│   └── lib/
│       └── stories.ts          # data/stories_latest.json 로드
└── data/
    ├── lifecycle/
    │   └── YYYY-MM-DD.json     # 일자별 스냅샷 + 상태 라벨
    └── stories_latest.json     # UI가 읽는 최신 스토리 + 상태
```

### 12.4 데이터 모델 추가

**`data/lifecycle/YYYY-MM-DD.json`** (일자별 스냅샷):
```json
{
  "date": "2026-05-27",
  "stories": [
    {
      "story_id": "story_2026-05-27_001",
      "title": "엔비디아 GTC 키노트 후 AI 인프라 수주 확대",
      "tickers": ["NVDA", "AVGO"],
      "score": 0.87,
      "state": "evolving",
      "parent_story_id": "story_2026-05-26_003",
      "linked_at": "2026-05-27T09:12:00Z",
      "similarity": 0.81,
      "event_ids": ["evt_..."]
    }
  ]
}
```

**`data/stories_latest.json`** (UI 소비용, 매 batch 갱신):
```json
{
  "generated_at": "2026-05-27T09:15:00Z",
  "stories": [{ /* 위와 동일 + narrative_short */ }]
}
```

### 12.5 새 CLI 서브커맨드

- `finvision lifecycle link --date 2026-05-27` — 오늘 스토리에 어제 스토리 연결, 상태 라벨 작성, 스냅샷 저장
- `finvision lifecycle list --days 7` — 최근 7일 동안 active/evolving/resolved 추이
- `batch top30 --days 7` 끝에 `lifecycle link` 자동 호출 (편의)

### 12.6 2주 일정

| Week | Day | 작업 | 검증 |
|---|---|---|---|
| 1 | 1~2 | `lifecycle/store.py` + 스냅샷 JSON 스키마 | 오늘 스토리 → JSON 저장/로드 round trip |
| 1 | 3~4 | `lifecycle/link.py` (임베딩+ticker 매칭) | 어제·오늘 동일 사건 정상 연결 |
| 1 | 5~6 | `lifecycle/state.py` (active/evolving/resolved 판정) | 3일 무신호 시 resolved 전환 |
| 1 | 7 | `cli.py` `lifecycle` 서브커맨드 + 통합 테스트 | 2일 연속 batch 실행 시 상태 정상 |
| 2 | 8 | Next.js 14 프로젝트 부트스트랩 + Tailwind 셋업 | `ui/` 빌드/dev 동작 |
| 2 | 9~10 | `/today` 페이지: Top 10 카드 + 상태 배지 | json 로드해서 렌더 |
| 2 | 11~12 | narrative 펼침 / ticker 클릭 → 필터 | 정적 인터랙션 동작 |
| 2 | 13 | 백엔드 산출물 ↔ UI 연동 final + 스타일링 | `localhost:3000` 보기 좋게 |
| 2 | 14 | 평가 | §12.8 통과 |

### 12.7 비용 / 의존성

- **추가 LLM 호출 없음** — lifecycle 매칭은 기존 임베딩 재사용
- npm 의존성: `next@14`, `react`, `tailwindcss`, `clsx`. 빌드 정적/dev only
- 호스팅: M4 동안 `localhost` only (M5에서 Vercel 등 검토)

### 12.8 평가 기준 (Day 14)

| 항목 | 기준 | 미달 시 |
|---|---|---|
| Lifecycle 연결 | 2일 연속 batch에서 어제 Top 5 중 3개+ 가 오늘 evolving으로 연결 | 유사도 threshold 조정 |
| 상태 라벨 정확성 | resolved 전환된 스토리가 정말 새 신호 없는가 수동 5건 확인 | 무신호 기간 파라미터 조정 |
| UI 시각화 | `/today`에서 Top 10 + 상태 배지 한 화면에 보이고, 클릭 시 narrative 펼침 | 디자인 단순화 |
| 본인 평가 | "아 이 스토리 어제부터 이어지는구나"가 한눈에 보임 | 표현 방식 재검토 |

---

## 13. M5 상세 명세 (본격 UI + 실시간 + 알림)

### 13.1 목표
혼자 쓰던 도구를 **팀원 3명**이 쓸 수 있게: 호스팅, 인증, 실시간 갱신, 푸시 알림.

### 13.2 합의된 설계 결정 (잠정, M4 종료 시 재확정)
- 호스팅: Vercel (UI) + GitHub Actions cron (batch 트리거)
- 인증: 매직링크 (이메일) 1-tier
- 실시간: 5분 폴링 (WebSocket은 과함)
- 알림: 이메일 (Resend) 우선, 푸시는 후순위

### 13.3 산출물 후보 (M4 종료 후 재확정)
- 멀티페이지 UI: `/today`, `/stories/:id`, `/timeline`, `/settings`
- 사용자별 watchlist (관심 ticker 필터)
- 일별 다이제스트 이메일
- 인시던트 알림 (스코어 ≥ 0.9 새 스토리 발생 시)

### 13.4 평가 기준
| 항목 | 기준 |
|---|---|
| 팀 사용성 | 팀원 3명이 매일 1번 이상 자발적 접속 |
| 실시간성 | 새 사건 발생 ~30분 내 UI 반영 |
| 알림 정확성 | 푸시 대비 false alert 비율 < 30% |

---

## 14. Phase 2 — 한국 시장 + 일반 서비스화 (별도 프로젝트)

### 14.1 배경
- 한국은 데이터 소스 (KRX/네이버/연합), 티커 매핑, 언어, 시장 시간이 모두 달라 미국 코드를 재사용하기보다 **별도 ingest/scoring path**가 필요
- M5 종료 후 미국 파이프라인이 안정화되면 동일 패턴으로 한국 추가

### 14.2 후보 작업
- KRX/네이버 뉴스 ingest 어댑터
- 한국 종목 universe (KOSPI200 + 코스닥150 핵심)
- 한·미 동시 표시 UI (탭 또는 통합 뷰)
- 일반 외부 사용자 모집 / 결제 / 다국어

---

## 15. 미결 사항 / 미래 결정 필요

| 항목 | 결정 시점 | 비고 |
|---|---|---|
| ~~한국 시장 5 + 미국 5 vs 뒤섞기~~ | ~~M3 직전~~ | **해소됨 (2026-05-27, Phase 2로 분리)** |
| Lifecycle 유사도 / ticker overlap threshold | M4 운영 시 | 0.75 / ≥1 기본, 실제 데이터로 튜닝 |
| Resolved 전환 무신호 기간 | M4 운영 시 | 3일 기본, 분야별로 다를 수 있음 |
| 미니 UI 호스팅 (localhost → Vercel) | M5 | 팀 공유 시점 |
| SNS/커뮤니티 수집 | M5 이후 | X API 유료, 비용 부담 |
| 그래프 시각화 (d3.js 인과 그래프) | M5 이후 | 미니 UI에서는 카드만 |
| 사용자 관심종목 모니터링 | M5 (watchlist) | 팀 사용 시점 |
| 영향력 점수 가중치 자동 튜닝 | M3 이후 | 사용자 라벨 누적 후 회귀 |
| 다국어 (영어 UI) | Phase 2 | |
| ~~Vertex grounding redirect URL 해석~~ | ~~M2 (필수)~~ | **해소됨 (2026-05-18, Tavily/Parallel 도입으로 자동 해결)** |
| Gemini 무료 tier quota 정책 (변동) | 운영 시 모니터링 | 모델별 일일 한도 ~20 RPD. 결제 활성 시 해소 |
| Tavily / Parallel 한도 관리 | M3 운영 시 | Tavily 1000/월, Parallel ~$5 크레딧. 한도 초과 시 알림 필요 |

---

## 16. 변경 이력

- **2026-05-18**: 초안 작성. 사용자와 5회 Q&A를 통해 모든 핵심 결정 합의.
- **2026-05-18 (저녁)**: 검색 백엔드를 Gemini Google Search grounding에서 **Tavily Search + Parallel Web Reader 조합 (패턴 A)** 으로 교체.
  - 원인: Gemini grounding이 무료 tier 한도 빡빡, redirect URL 단기 만료 문제
  - 영향:
    - §5.3.1 새 섹션 (검색·추출 아키텍처) 추가
    - §6 기술 스택: Tavily / Parallel Reader 신규 / LLM은 Flash Lite로 한정
    - §9.2 프로젝트 구조: `research/search.py`, `research/reader.py`, `llm.py` 추가
    - §9.3 의존성: `tavily-python`, `parallel-web` 추가
    - §9.7 비용 추정: ~$0.40 → **~$0.05~0.10 / 실행** 으로 절감
    - §11 grounding redirect 미결 사항 해소
- **2026-05-18 (저녁 2차)**: SDK 검토 중 Parallel이 search+extract 둘 다 제공함을 확인. **"기능별 분리" → "깊이별 분리"** 로 재설계.
  - 변경: 얕은=Tavily 단독 / 깊은=Parallel `session_id`로 search+extract 묶음
  - 이유: 같은 session 안에서 search↔extract 연계 시 품질 최적화. 다른 벤더로 쪼개면 이 이점 사라짐
  - 영향:
    - §5.3.1 재작성 (깊이별 분리 + 효율 트릭 명시)
    - §9.2 파일명 변경 (`search.py`/`reader.py` → `tavily_client.py`/`parallel_client.py`)
    - §9.4 Day 8~11 설명 갱신
    - §9.7 비용: ~$0.10~0.20 / 실행 (Parallel이 search도 하므로 약간 증가)
- **2026-05-22**: **M2 Day 1~10 코드 완료** (edges → graph → narratives → report).
  - 신규 모듈: `src/causal/{schema,edges,graph,story}.py`, `src/report/markdown.py` Story 단위 재작성
  - 신규 CLI: `edges`, `stories`, `narratives`, 갱신된 `report`
  - 산출물 흐름: `scored + research → edges → stories → narratives → report`
  - 검증된 동작: NVDA Top 20 → 5 edges 발견 → 15 Story → 10 narrative 생성 → Markdown 리포트 (~24K자)
  - 30개 단위 테스트 통과
  - Day 11~12 (E2E 통합) 완료 — fresh ingest~report까지 전 파이프라인 정상 동작
  - Day 13~14 (사용자 평가) 통과 (2026-05-22 리뷰)
- **2026-05-23**: claim 추출 단계 한국어화 + LLM 검증 추가.
  - 변경: `infer_from_claims`에 `_verify_claim_edge` 호출 — 후보 → 일치 검증 + 한국어 메커니즘 생성
  - 효과: false positive 60% 제거 (5→2), 메커니즘 모두 자연 한국어, direction 라벨 정확화, 신뢰도 0.6→0.85 상승
- **2026-05-24**: **M3 착수**. §11 신설.
  - 합의: 30 ticker (섹터균형) / 무료 tier / 신호 먼저 (Week 1) → 스케일 (Week 2)
  - §12 (미결사항), §13 (변경이력)로 번호 시프트
- **2026-05-25**: M3 Day 1~13 코드 완료.
  - Day 1~2 Novelty: `src/score/novelty.py`. 흔한 NVDA 일상 분석 강등, 신규 사건 부각
  - Day 3~4 PriceReaction: `src/score/price_reaction.py`. 실제 시장 반응 큰 사건(Astera +16.5%, Poet 급락) 부각
  - Day 5~7 4신호 검증: NVDA Top 10 다양성 향상, 인과 edge는 트레이드오프로 감소
  - Day 8~9 다종목 ingest: `src/universe/seeds.py` + `ingest --universe`. top30=30종목 7일 → 390 unique articles
  - Day 10~11 다종목 풀 파이프라인: 326 events → **Top 10 스토리 100% 멀티티커** (소비재/금융 자연 등장)
  - Day 12~13 cost_guard + batch CLI: `src/cost_guard.py` SQLite 추적, `batch` 8단계 one-shot, `costs` 사용량
  - 52 단위 테스트 통과
- **2026-05-18 (밤)**: M1 코드 완료 (Day 1~13). Day 14 본인 평가는 보류, M2로 진행.
  - M1 종단 검증: NVDA 10 shallow + 3 deep + Markdown 리포트 모두 정상
  - 한국어 출력 적용: shallow/deep 합성 프롬프트에 "한국어로 작성" 지시, report 라벨 한국어화
  - **M2 상세 명세 §10 신설** — Causal Graph + Story 생성 (2주 일정)
    - 인과 edge 추론: Top 20 pairwise LLM + deep research claim 재활용
    - Story 크기: 1~20 노드, 단일 이벤트도 인정
    - 다종목 지원 (그래프 자연스럽게 멀티티커)
    - 비용 최적화: ticker/시간/임베딩 사전 필터로 190쌍 → 30~60쌍
  - §11 (미결사항), §12 (변경이력)로 번호 시프트
- **2026-05-30**: M4 Day 13 — 스타일링 final.
  - **헤더 sticky** (`top-0` + `backdrop-blur` + 반투명 배경) — 카드 스크롤 시 날짜·카운트 항상 보임. 필터 활성 시 두 번째 줄도 sticky `top-[88px]` 로 헤더 아래 붙음.
  - **카드 hover lift** (`hover:-translate-y-0.5 hover:shadow-md`) + **상태별 좌측 4px 색 인디케이터** (active=green / evolving=yellow / resolved=zinc) — 스크롤 중에도 상태가 한눈에.
  - **ScoreBar 점수 구간 색**: ≥0.8 indigo-600, ≥0.5 indigo-500/80, ≥0.3 slate-500, 그 외 zinc-400.
  - **StateBadge `title` tooltip** — active/evolving/resolved 각각의 의미 호버 설명 + `cursor-help`.
  - **빈 상태 그래픽**: no-file 🗂️ + 명령 코드 박스, no-stories 📭. 필터 결과 0 시 🔍 + "필터 해제" 빠른 액션.
  - **필터 바에 (N/M 표시)** 추가, 카드 hover 트랜지션과 ScoreBar 색 전환에 `transition` 적용.
  - 검증: build 통과 (`/today` 2.77 kB 클라이언트), curl HTML 에 sticky 1 · backdrop-blur 1 · cursor-help 10 · hover:-translate-y 10 · border-l-evolving 10 · bg-indigo 20 모두 확인.
- **2026-05-29**: M4 Day 11~12 — narrative 펼침 + ticker 필터.
  - **backend**: `LifecycleStory` 에 `narrative_long: str = ""` 추가, `from_story` 가 `Story.narrative_long` 복사. 42 단위 테스트 유지.
  - **UI 분리**: `lib/stories.ts` (타입 + 순수 헬퍼) / `lib/stories-server.ts` (`server-only` + fs/path) — Client Component 가 `countByState` 만 import 해도 node:fs 가 클라이언트 번들로 끌려가던 빌드 오류 해결.
  - **UI 인터랙션**: `components/TodayBoard.tsx` (Client) 가 `selectedTickers`/`expanded` state 보유. `StoryCard` 의 ticker 칩이 toggle 버튼화 (aria-pressed), 클릭 시 필터 추가, 다중 선택은 OR, 활성 시 상단 필터 바 + "모두 해제" 노출. narrative_long 있으면 카드 하단 "더 보기 / 접기" 토글로 확장 영역 표시.
  - 검증: build 통과 (`/today` 2.29 kB 클라이언트 코드), curl /today HTML 에 67 button, 57 aria-pressed, 10 "더 보기", 30 narrative_long 페이로드 확인.
- **2026-05-28 (저녁)**: M4 Day 9~10 — `/today` 페이지 실데이터 연결.
  - `ui/lib/stories.ts`: `LifecycleStory`/`StoriesLatest` 타입 + `readStoriesLatest` (fs로 ../data/stories_latest.json 읽기, ENOENT는 null), `topStories` (빈 제목 필터 + 점수 내림차순 + 첫 등장일 동점 처리), `countByState`. `STORIES_LATEST_PATH` env 오버라이드 지원.
  - `ui/app/today/page.tsx`: Server Component (`force-dynamic`). 헤더에 날짜 + 총량 + 상태별 카운트, Top 10 카드 그리드. 각 카드: 순위, 제목, 🟢/🟡/⚫ StateBadge, ScoreBar, 첫 등장일, evolving 시 유사도(%), 티커 칩 (8개 + 잔여 +N), narrative_short. 빈 상태 friendly 안내 메시지.
  - 검증: `npm run build` 통과 (`/today` Dynamic 전환). `next start` 으로 띄워 curl /today → 10 evolving 카드 + 헤더 "총 20개 🟢10 🟡10 ⚫0" 정상 렌더 확인.
- **2026-05-28**: M4 Day 8 — Next.js 14 + Tailwind 3 부트스트랩.
  - `ui/` 디렉터리: `package.json`, `next.config.js`, `tsconfig.json` (strict), `tailwind.config.ts` (active/evolving/resolved 색 토큰), `postcss.config.js`, `.gitignore`
  - 앱 스켈레톤: `app/layout.tsx` (한국어 lang, 최대 5xl 컨테이너), `app/page.tsx` (→ `/today` 리다이렉트), `app/today/page.tsx` (상태 배지 placeholder)
  - Next 14.2.35 (Dec 2025 보안 패치 적용 버전), npm install + `npm run build` 통과 — `/today` `/` 모두 정적 prerender
- **2026-05-30 (저녁)**: **M3.5 분석 깊이 확장 — Day 1~8 모두 완료**.
  - 배경: 미니 UI 가 종목 중심이라 거시 관점·파급효과가 빠짐. 사용자 요청으로 M5 보류 + M3.5 신설.
  - **Day 1~2 테마 클러스터링** (`src/macro/themes.py`): Story 단위 임베딩 + Union-Find (sim ≥ 0.70), LLM 1회로 한국어 테마명/설명 명명 ("AI 인프라 자본지출 가속" 등). 13 단위 테스트.
  - **Day 3 FRED 거시지표** (`src/macro/fred.py`): 8개 시계열 (FEDFUNDS / CPIAUCSL / DGS10 / DCOILWTICO / DEXJPUS / UNRATE / T10Y2Y / VIXCLS) fetch + 1σ 이상 변화 자동 detect → MacroEvent. 12 단위 테스트. 라이브 검증 — 60일 lookback 에서 29개 거시 이벤트 발견 (WTI $112→$101 등).
  - **Day 4 macro+theme 통합**: `lifecycle/store.py` Snapshot 에 `macro_events` / `themes` 필드, `cmd_lifecycle_link` 가 자동 fetch+클러스터. graph 합류는 안 함 (별개 stream — 단순화).
  - **Day 5~6 RippleEffect** (`src/causal/ripple.py`): `causal/schema.py` 에 RippleEffect 추가 (tier=direct/adjacent/macro × horizon=1w/1m/1q × direction × confidence × 한국어 mechanism). narratives 단계에 LLM 1콜 추가, Story.ripple_effects 채움. 18 단위 테스트. 라이브 — 스토리당 6~8개 ripple 자연스럽게 생성.
  - **Day 7~8 UI 통합**: `ThemeStrip.tsx` (가로 스크롤 카드 + 클릭 필터), `MacroPanel.tsx` (▲▼ 한국어 요약), `RippleSection.tsx` (tier 별 그룹 + ▲▼ 글리프 + horizon/conf%). StoryCard "더 보기" 펼침 시 narrative_long + RippleSection 동시 노출. TodayBoard 가 themeFilter ↔ tickerFilter AND 결합.
  - 검증: top30 batch 실데이터로 — 39 stories / 10 themes / 4 macro / 74 ripple effects. UI `npm run build` 4.19 kB 클라이언트 코드 (3.5x 증가, 인터랙티브 컴포넌트 증가분).
  - **테스트 총계: 137 → 변경 후 확인 필요**. 비용 추가: +$0.02~0.03 / batch.
- **2026-05-27 (밤)**: M4 Week 1 (Day 1~7) 코드 완료.
  - Day 1~2: `src/lifecycle/store.py` — `LifecycleStory`/`Snapshot` pydantic, save/load/list/prev (12 테스트)
  - Day 3~4: `src/lifecycle/link.py` — 어제↔오늘 매칭 (ticker overlap ≥ 1 + 임베딩 cos sim ≥ 0.75), 빈 텍스트 가드 (15 테스트)
  - Day 5~6: `src/lifecycle/state.py` — active/evolving/resolved 라벨 + 이월 처리 + 무신호 3일 → resolved (15 테스트)
  - Day 7: `cli.py` `lifecycle link/list` 서브커맨드 + `batch` 9단계로 확장 + `data/stories_latest.json` (UI 소스) 생성
  - 검증: NVDA narratives 2일 연속 lifecycle link → Top 10 모두 evolving 으로 연결 (§12.8 ① 통과)
  - 전체 94 단위 테스트 통과 (M3 52 + M4 42)
- **2026-05-27**: **M4 범위 재정의**. 한국 시장 → Phase 2 분리, M4 = Story Lifecycle + 미니 UI (D-2 선택).
  - 결정: 한국은 데이터 소스·티커 매핑·언어가 다 달라 별도 프로젝트로 분리해야 효율적. M5까지 미국에서 파이프라인 완성 후 Phase 2.
  - M4 = Story Lifecycle (active/evolving/resolved) + Next.js 미니 UI (`/today` 단일 페이지)
  - M5 = 본격 웹 UI + 실시간 + 알림 (팀 사용성)
  - Phase 2 = 한국 시장 + 일반 서비스화
  - 신규: §12 (M4 명세), §13 (M5 명세), §14 (Phase 2 한국)
  - §15 (미결사항), §16 (변경이력)로 번호 시프트
