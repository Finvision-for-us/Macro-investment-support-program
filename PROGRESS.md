# FinVision 개발 진행 상황

> 마지막 업데이트: 2026-06-22

---

## 프로젝트 개요

미국 주식 분석 플랫폼. 거시경제 분석, 종목 분석, 포트폴리오 관리, AI 심층 리서치 기능을 포함.
토스증권에 없는 기능을 목표로 개발 중. Gemini API 크레딧 16일 남음 (₩40만원 한도).

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| 프론트엔드 | React 18 + Vite + Tailwind CSS + Recharts + Zustand |
| 백엔드 | FastAPI + aiosqlite (SQLite) |
| AI | Gemini 2.5 Flash (가이던스/번역/분석) |
| 데이터 | Yahoo Finance, SEC EDGAR, FRED, Finnhub, NewsAPI |
| 실행 | localhost:5173 (프론트) / localhost:8000 (백엔드) |

---

## 완료된 기능

### 거시경제 탭
- [x] FRED 12개 지표 실시간 조회 (GDP, CPI, 실업률, 연방금리, 장단기금리차 등)
- [x] 8단계 경기 사이클 분류 (NBER 기반, Z-score 정규화)
- [x] 현재 사이클 단계 시각화 (CycleDiagram)
- [x] 단계별 추천/주의 섹터 표시
- [x] 경제 뉴스 피드 (NewsAPI)
- [x] Gemini AI 거시경제 분석

### 종목 분석 탭
- [x] 종목 검색 (로컬 DB + Yahoo Finance, 한국어 종목명 지원)
- [x] 회사 개요 (시총, PER, PBR, 배당수익률, 다음 실적 발표일)
- [x] 기업 설명 한국어 번역 (Gemini, MD5 캐시)
- [x] 주가 차트 (1일~전체, Recharts)
- [x] 재무 요약 (매출, 순이익, ROE, 부채비율 등 30개+ 지표)
- [x] SEC 공시 목록 (10-K, 10-Q, 8-K, DEF 14A)
- [x] 관련 뉴스 피드 (종목별 15개)
- [x] Gemini AI 종목 분석 (밸류에이션, 리스크, 기회)

### AI 가이던스 분석
- [x] SEC 8-K 프레스 릴리스 → Gemini 분석 (경영진 가이던스 추출)
- [x] 분기별 sentiment_score (0~100), 가이던스 요약, 핵심 테마
- [x] 매출/마진 가이던스 수치 추출
- [x] 결과 DB 영구 캐시 (재분석 불필요)
- [x] 가이던스 적중률 (AI 예측 vs 실제 실적 비교)
- [x] 애널리스트 vs AI 비교

### AI 경쟁사 분석
- [x] 사업 영역별 직접 경쟁사 선정 (섹터 분류 아닌 실제 사업 기반)
- [x] 경쟁사 재무 지표 비교 테이블
- [x] 결과 DB 캐시

### AI 핵심지표 추천
- [x] 종목별 맞춤 지표 7~10개 선정
- [x] 실제 재무 데이터를 Gemini에게 넘겨 데이터 기반 선정으로 개선
- [x] 지표별 status (good/warning/danger) + 실제 수치 근거 포함
- [x] risk_summary (강점/리스크 2문장) 추가

### 실적 시뮬레이터
- [x] EPS 어닝 서프라이즈 히스토리 (4개 소스 합산: SEC, Yahoo, Finnhub, Alpha Vantage)
- [x] 어닝 발표일 전후 주가 반응 계산 (pre-3d, 1d, post-3d, post-5d)
- [x] 요인별 가중치 (EPS/매출/가이던스 중 어느 것이 주가에 더 영향?)
- [x] 실적 발표 시나리오 시뮬레이션

### 실적 캘린더
- [x] 해당 종목 + 경쟁사 실적 발표 일정

### 포트폴리오 탭
- [x] 종목 추가/삭제
- [x] 실시간 수익률 계산 (Yahoo Finance 현재가 기준)
- [x] 종목별 상세 분석 뷰

### AI 심층 리서치 탭
- [x] 멀티에이전트 파이프라인 (Planner → Searcher → Critic → Synthesizer)
- [x] 다중 소스 검색 (Tavily, SEC EDGAR, FRED, arXiv, Jina)
- [x] 리서치 계획 생성 → 사용자 승인 → 실행
- [x] SSE 스트리밍으로 실시간 진행 상황 표시
- [x] 세션 저장/불러오기

### 배치 분석 시스템
- [x] S&P 500 503개 종목 배치 분석 스크립트 (`batch_analyze.py`)
- [x] 가이던스 분석 + 경쟁사/지표 프로필 + 한국어 번역 일괄 처리
- [x] 이미 분석된 종목 자동 스킵 (캐시 기반)
- [x] 로그 파일 저장 (`batch_analyze.log`)

---

## 진행 중 / 이슈

### Gemini 모델 업그레이드 (오늘 완료)
- `gemini-2.5-flash-lite` → `gemini-2.5-flash` 전체 교체
- 대상 파일: `gemini_guidance.py`, `stock_profile_ai.py`, `ai_client.py`, `stock.py`, `batch_analyze.py`
- S&P 500 전체 기준 예상 비용: ~$3 (₩4,000)

### 배치 분석 현황
- 현재 약 4~11종목 완료 (A, AAPL, ABBV, ABNB...)
- Motley Fool IP 차단 → SEC 8-K 1순위로 변경 후 정상 작동
- 한 종목당 평균 30~90초 소요
- 503종목 완료까지 약 4~8시간 예상

### Motley Fool 차단
- 대량 요청으로 IP 블랙리스트 등록 (HTTP 429)
- SEC 8-K 프레스 릴리스로 대체 (공공 데이터, 차단 없음)
- 차이: 8-K는 숫자 위주, 트랜스크립트는 경영진 뉘앙스 포함
- 24~48시간 후 차단 해제되면 트랜스크립트 보강 가능

---

## 미완료 / 계획 중

### 핵심지표 선정 방식 개선 (논의 중)
- 현재: Gemini가 재무 데이터 보고 추론
- 목표: 업종별 애널리스트 표준 KPI 하드코딩 (더 정확, Gemini 토큰 0 소모)
  - Banks → NIM, NPL, CET1
  - SaaS → ARR growth, NRR, churn
  - Pharma → R&D ratio, pipeline count
  - Oil&Gas → reserve replacement, production cost
  - REIT → FFO, NAV, occupancy

### 뉴스 품질 개선 (논의 중)
- 현재: NewsAPI.org (기본 품질)
- 목표: Yahoo Finance 뉴스 + Gemini 한국어 요약

### 가이던스 분석 → Pro 모델 적용 (논의 중)
- 가이던스만 `gemini-2.5-pro`로 업그레이드 ($42/S&P500)
- 나머지는 Flash 유지

### MiroFish 뉴스 임팩트 시뮬레이션 (장기 목표)
- 한 기사가 종목에 미치는 영향 시뮬레이션
- 40년치 S&P 500 데이터 기반
- 중국 퀀트 트레이더 시스템에서 영감

---

## DB 테이블 현황

| 테이블 | 내용 | 상태 |
|--------|------|------|
| `portfolio` | 사용자 보유 종목 | 운영 중 |
| `earnings_surprises` | 어닝 서프라이즈 히스토리 | 운영 중 (4소스 합산) |
| `earnings_price_reactions` | 어닝 전후 주가 반응 | 운영 중 |
| `cache_metadata` | 캐시 갱신 시각 | 운영 중 |
| `guidance_analysis` | AI 가이던스 분석 결과 | 배치 채우는 중 |
| `stock_profile_ai` | 경쟁사 + 핵심지표 | 배치 채우는 중 |
| `translation_cache` | 한국어 번역 캐시 | 배치 채우는 중 |
| `research_sessions` | 심층 리서치 세션 | 운영 중 |
| `research_messages` | 리서치 채팅 메시지 | 운영 중 |

---

## 알려진 버그 / 제한사항

| 항목 | 내용 |
|------|------|
| 미리보기 도구 오류 | Claude Code preview_start가 한글 경로(`정치훈`) 처리 불가 — Bash로 직접 실행 |
| 서버 자동 종료 | Claude Code 세션 종료 시 서버도 꺼짐 — 매번 재시작 필요 |
| Motley Fool 차단 | IP 블랙리스트 — SEC 8-K로 대체 운영 중 |
| Finnhub 트랜스크립트 | 무료 tier 미지원 — 유료 전용 |
| 가이던스 배치 속도 | Gemini rate limit으로 종목당 30~90초 소요 |

---

## 외부 API 사용 현황

| API | 키 보유 | 용도 | 한도 |
|-----|--------|------|------|
| Google Gemini | ✅ | 가이던스/번역/분석/심층리서치 | ₩40만원 크레딧, 16일 |
| FRED | ✅ | 거시경제 지표 | 무료 무제한 |
| Finnhub | ✅ | 어닝 서프라이즈/캘린더 | 무료 (60req/min) |
| NewsAPI | ✅ | 뉴스 | 무료 (100req/day) |
| SEC EDGAR | ✅ (헤더) | 공시/8-K/CIK | 무료 무제한 |
| Yahoo Finance | ✅ (키 없음) | 주가/재무 | 무료 (제한적) |
| Tavily | ✅ | 심층리서치 웹검색 | 유료 크레딧 |
| Alpha Vantage | ✅ | 미사용 (설정만) | 유료 |

---

## 파일 구조 요약

```
finvision/
├── backend/
│   ├── app/
│   │   ├── api/          # 엔드포인트 (macro, stock, portfolio, earnings)
│   │   ├── services/     # 데이터 수집 + AI 분석
│   │   └── deep_research/ # 멀티에이전트 심층 리서치
│   ├── batch_analyze.py  # S&P 500 배치 분석
│   └── finvision.db      # SQLite DB
└── frontend/
    └── src/
        ├── components/
        │   ├── views/    # 4개 메인 뷰
        │   └── shared/   # 12개 공통 컴포넌트
        ├── api/          # Axios 클라이언트
        └── store/        # Zustand 상태관리
```
