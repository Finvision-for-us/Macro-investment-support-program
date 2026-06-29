# AI 의존성 감사 보고서 (AI_REMOVAL_AUDIT)

> 작성일: 2026-06-29
> 목적: Claude API 제거, 일반 AI 종합분석 잔재 정리, 유지 대상 AI 기능 목록화

---

## 1. 제거된 Claude 관련 항목

| 항목 | 파일 | 변경 내용 |
|------|------|-----------|
| `CLAUDE_API_KEY` 환경변수 로드 | `backend/app/config.py` | 해당 줄 삭제 |
| `CLAUDE_API_KEY=` 항목 | `.env` | 해당 줄 삭제 (값 없음 확인) |
| `CLAUDE_API_KEY=your_claude_api_key_here` | `.env.example` | 삭제 후 GEMINI_API_KEY 설명으로 대체 |
| `CLAUDE_API_KEY=` (환경변수 예시) | `AGENTS.md` | 삭제, GEMINI_API_KEY 추가 |
| `CLAUDE_API_KEY=` (환경변수 예시) | `CLAUDE.md` | 삭제, GEMINI_API_KEY 추가 |
| `CLAUDE_API_KEY=  # console.anthropic.com` | `README.md` | 삭제, GEMINI_API_KEY 설명으로 대체 |

**결론**: Anthropic SDK import, Claude client 생성, Claude 직접 호출 코드는 codebase에 없음.
`ai_client.py`는 이미 Gemini 전환 완료 상태 (주석에도 명시: "Claude API 의존 제거").

---

## 2. 수정된 README 항목

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| API 키 설명 | `CLAUDE_API_KEY= # 없으면 AI 버튼 비활성` | `GEMINI_API_KEY= # AI 기능 전체` |
| 기능표 | 3개 탭 (거시/종목/포트폴리오) | 5개 탭 (실적 분석, 심층 리서치 추가) |
| AI 기능 설명 | 없음 | "Gemini 기반, Claude 미사용" 명시 섹션 추가 |
| 데이터 소스 | 4개 소스 | Finnhub, Alpha Vantage, Tavily, Parallel.ai 추가 |

---

## 3. Dead Code 정리 (프론트엔드에서 호출 없는 항목)

| 항목 | 파일 | 처리 |
|------|------|------|
| `stockAPI.aiAnalyze(ticker)` | `frontend/src/api/index.js` | **삭제** — StockDetail.jsx에서 호출 없음 확인 |
| `POST /{ticker}/ai-analyze` endpoint | `backend/app/api/stock.py` | `# DEPRECATED` 주석 추가 — 다음 단계에서 제거 |

---

## 4. 남아 있는 일반 AI 종합분석 잔재 (제거 후보)

### 4-1. MacroView "AI 시장 분析" 버튼
- **파일**: `frontend/src/components/views/MacroView.jsx` line 231–248
- **API**: `macroAPI.aiAnalyze()` → `POST /api/macro/ai-analyze`
- **백엔드**: `backend/app/api/macro.py` line 36–39 → `ai_client.analyze_macro()`
- **모델**: `ai_client.py` → Gemini Flash-Lite (Claude 아님)
- **Deep Research 중복 여부**: 부분 중복. 거시경제 AI 분석을 원한다면 Deep Research 탭에서
  직접 쿼리 가능. MacroView 버튼은 간단한 요약 수준.
- **판단**: 기능은 작동하나 Deep Research와 중복. 사용자와 협의 후 제거 검토 필요.

### 4-2. `POST /api/stock/{ticker}/ai-analyze` endpoint
- **파일**: `backend/app/api/stock.py` line 444–452
- **호출처**: 없음 (프론트엔드에서 이미 제거됨)
- **판단**: 완전한 dead code. 다음 PR에서 삭제 가능.

### 4-3. `ai_client.py` `analyze_stock()` 메서드
- **파일**: `backend/app/services/ai_client.py` line 41–65
- **호출처**: `stock.py`의 deprecated endpoint에서만 호출
- **판단**: endpoint 제거 시 함께 제거 가능.

---

## 5. 유지 확인된 AI 기능 목록

| 기능 | 파일 | 모델 | 상태 |
|------|------|------|------|
| 심층 리서치 파이프라인 | `backend/app/deep_research/` | Flash(Planner/Critic) + Pro(Synthesizer) | ✅ 유지 |
| 종목 프로필 AI 분석 | `backend/app/services/stock_profile_ai.py` | Gemini Flash | ✅ 유지 |
| 가이던스 AI 분석 | `backend/app/services/gemini_guidance.py` | Gemini Flash | ✅ 유지 |
| 기업 소개 번역 | `backend/app/api/stock.py` `/translate` | Gemini Flash | ✅ 유지 |
| 애널리스트 vs AI | `frontend/src/components/shared/AnalystVsAI.jsx` | guidance_analysis DB 기반 | ✅ 유지 |
| AI 경쟁사 분석 UI | `frontend/src/components/shared/StockDetail.jsx` | stock_profile_ai DB 기반 | ✅ 유지 |
| 거시경제 AI 분析 버튼 | `frontend/src/components/views/MacroView.jsx` | Gemini Flash-Lite (ai_client) | ⚠️ 제거 후보 |
| Gemini Langfuse 로거 | `research_lab/gemini_langfuse_log_runner/` | — | ✅ 유지 |

---

## 6. 다음 단계에서 검토할 AI/프롬프트 목록

> 이번 작업에서 **수정하지 않은** 항목들. 별도 단계에서 사용자와 하나씩 검토.

### 6-1. stock_profile_ai.py 프롬프트
- 경쟁사 선정 로직 (PROFILE_PROMPT) — 업종별 표준 KPI 하드코딩 전환 검토 중

### 6-2. Deep Research 에이전트 프롬프트
- `planner.py` PLAN_PROMPT — 2026-06-29 강화 완료, 추가 조정 가능
- `critic.py` CRITIC_PROMPT — 2026-06-29 강화 완료
- `synthesizer.py` NARRATIVE_PROMPT / EXTRACTION_PROMPT / VERIFY_PROMPT

### 6-3. gemini_guidance.py 프롬프트
- SEC 8-K 기반 가이던스 분석 프롬프트 — 트랜스크립트 소스 확보 시 재설계 필요

### 6-4. ai_client.py (MacroView용)
- `analyze_macro()` 프롬프트 — MacroView 버튼 제거 결정 시 함께 삭제

### 6-5. batch_analyze.py 번역 프롬프트
- `translate_description()` 내 번역 프롬프트 — 현재 양호

---

## 7. 검색 결과 확인

```
# 이 단어들은 현재 코드에 없음 (제거 완료)
grep -r "CLAUDE_API_KEY" backend/ frontend/ → 결과 없음
grep -r "anthropic" backend/ frontend/ → 결과 없음
grep -r "from anthropic" backend/ → 결과 없음
```

---

# 2차 정리 (2026-06-29)

## 8. /stock/{ticker}/ai-analyze endpoint — 완전 삭제 완료

| 확인 항목 | 결과 |
|-----------|------|
| `aiAnalyze` 검색 | macro용만 존재 (`macroAPI.aiAnalyze`). stock용 호출처 **없음** |
| `ai-analyze` 검색 | `macro.py`(유지), `stock.py`(삭제 대상)만 |
| frontend `stockAPI.aiAnalyze` | 1차 작업에서 이미 삭제됨 — 재확인 완료 |

**조치**:
- `backend/app/api/stock.py`의 `POST /{ticker}/ai-analyze` endpoint **완전 삭제**
- 사용처 없는 `from app.services.ai_client import ai_client` import도 **삭제**
- `get_stock_news` import는 line 223에서 여전히 사용 중 → 유지
- `yfinance_client`는 전역 사용 → 유지

**삭제 안전성 확인**: stock.py에서 `ai_client`는 삭제된 endpoint에서만 사용됐음.
다른 파일에서 stock의 ai-analyze를 참조하는 코드 없음. compileall 통과.

## 9. MacroView "AI 시장 분석" 기능 조사 결과

| 항목 | 내용 |
|------|------|
| **파일** | `frontend/src/components/views/MacroView.jsx` line 231–248 (버튼), 304–315 (결과 출력) |
| **기능명** | "AI 시장 분석" (버튼 라벨: 로딩 중 "AI 분석 중...") |
| **호출 API** | `macroAPI.aiAnalyze()` → `POST /api/macro/ai-analyze` |
| **백엔드** | `backend/app/api/macro.py` line 36–39 → `ai_client.analyze_macro(cycle_state)` |
| **사용 모델** | `ai_client.py` → **Gemini 2.5-flash-lite** (Claude 아님) |
| **입력 데이터** | 현재 경기 사이클 상태 (GDP QoQ, 실업률, CPI YoY, 기준금리) |
| **출력** | 한국어 3–4문단: 사이클 위치 / 거시 리스크 / 투자 주목점 |
| **Deep Research 중복 여부** | **부분 중복**. Deep Research는 종목/쿼리 기반 심층 리서치. MacroView AI는 이미 계산된 사이클 지표를 1클릭으로 요약하는 대시보드 전용 quick-take. UX·범위가 다름 |
| **추천** | **유지 보류** (즉시 삭제 안 함) |
| **이유** | ① Gemini 기반이라 Claude 제거 목적과 무관 ② 거시 대시보드 전용 경량 기능으로 Deep Research와 사용 맥락이 다름 ③ 비용 낮음(flash-lite, 800토큰). 단, 향후 Deep Research 거시 쿼리로 일원화 시 제거 후보 |

## 10. .env 커밋 위험 점검 결과

| 확인 항목 | 결과 |
|-----------|------|
| `.env` git tracked 여부 | **untracked** (git에 없음) ✅ |
| `.gitignore`에 `.env` 포함 | 포함됨 (line 2: `.env`, line 29/46: `.env.*`, `!.env.example` 예외) ✅ |
| `.env.example` tracked | tracked (의도된 커밋 대상) ✅ |
| `.env.example` 실제 키 포함 | **없음** — 전부 placeholder ✅ |

**결론**: `.env` 커밋 위험 없음. 민감정보가 git에 들어가지 않도록 이미 올바르게 구성됨.
별도 조치 불필요. (참고: `backend/backend.err`, `frontend/frontend.err`는 서버 실행 산출물 —
필요시 `.gitignore` 추가 고려, 이번 작업 범위 아님)

## 11. 2차 정리 후 유지 중인 AI 기능 (변동 없음)

심층 리서치 파이프라인, stock_profile_ai, gemini_guidance, AnalystVsAI,
번역/경쟁사/핵심지표, Gemini Langfuse runner, **MacroView AI 시장 분석(유지 보류)** —
모두 유지.

## 12. 다음 단계 프롬프트 개선 대상 (갱신)

1. `stock_profile_ai.py` PROFILE_PROMPT — 업종별 표준 KPI 하드코딩 전환
2. Deep Research `planner/critic/synthesizer` 프롬프트 — 추가 미세조정
3. `gemini_guidance.py` — 트랜스크립트 소스 확보 시 재설계
4. `ai_client.py analyze_macro()` — MacroView 일원화 결정 시 프롬프트 정리 또는 삭제

## 13. 2차 검색 결과 확인

```
grep -rn "aiAnalyze"  → macro.py 호출만 (MacroView, 유지)
grep -rn "ai-analyze" → macro.py endpoint만 (유지). stock.py 삭제 완료
grep -rn "AI 종합분석" → 없음 (StockResearchChat의 "종합 분석"은 추천 쿼리 텍스트, 무관)
grep -rn "CLAUDE_API_KEY" → 없음
grep -rn "anthropic" → 없음
```
