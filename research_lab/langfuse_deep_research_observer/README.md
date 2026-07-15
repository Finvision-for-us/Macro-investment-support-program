# Langfuse Deep Research Observer

FinVision 서비스 코드와 분리된 실험용 도구입니다. Gemini Deep Research, OpenAI/ChatGPT Deep Research, FinVision Deep Research의 공개 가능한 실행 로그와 리서치 산출물을 같은 스키마로 정규화하고 비교합니다.

이 도구는 모델 내부의 비공개 chain-of-thought를 복원하거나 추정하지 않습니다. 사용자가 직접 복사해 온 로그, 검색어, tool call, citation, 중간 요약, 최종 답변만 분석합니다.

## 범위

- 위치: `research_lab/langfuse_deep_research_observer/`
- FinVision `backend/`, `frontend/`, DB, 서비스 라우팅은 수정하지 않습니다.
- 실제 Gemini/OpenAI API를 호출하지 않습니다.
- Langfuse 키가 없어도 로컬 비교 리포트는 생성할 수 있습니다.
- Langfuse Cloud 또는 self-host Langfuse에 trace 업로드가 가능합니다.

## 설치

```bash
pip install -r research_lab/langfuse_deep_research_observer/requirements.txt
```

Langfuse 업로드를 사용할 때만 환경변수를 설정합니다.

```bash
copy research_lab\langfuse_deep_research_observer\.env.example research_lab\langfuse_deep_research_observer\.env
```

`.env`:

```env
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

## 입력 파일

사용자가 복사한 로그를 `input/` 폴더에 넣습니다.

```text
research_lab/langfuse_deep_research_observer/input/
  gemini_log_sample.txt
  openai_log_sample.json
  finvision_log_sample.json
```

지원 형식:

- Gemini: 텍스트 로그 중심
- OpenAI/ChatGPT: JSON 우선, 실패 시 텍스트 fallback
- FinVision: JSON 우선

## Langfuse 업로드

```bash
python research_lab/langfuse_deep_research_observer/run_upload.py --type gemini --file research_lab/langfuse_deep_research_observer/input/gemini_log_sample.txt
```

지원 타입:

- `gemini`
- `openai`
- `finvision`

Langfuse 키가 없으면 업로드는 중단되며, 무엇이 빠졌는지 메시지를 출력합니다.

## 로컬 비교 리포트

```bash
python research_lab/langfuse_deep_research_observer/run_compare.py --gemini research_lab/langfuse_deep_research_observer/input/gemini_log_sample.txt --openai research_lab/langfuse_deep_research_observer/input/openai_log_sample.json --finvision research_lab/langfuse_deep_research_observer/input/finvision_log_sample.json
```

출력:

```text
research_lab/langfuse_deep_research_observer/output/
  comparison_raw_material.json
  comparison_report.md
```

## 비교 항목

총점은 100점입니다.

| 항목 | 점수 | 측정 방식(결정론) |
| --- | ---: | --- |
| jurisdiction_detection | 15 | 주장 관할 ∩ 증거(도메인/쿼리) 자카드 |
| query_generation | 15 | 앵커율·비중복률·공식쿼리비중·다국어 평균 |
| official_source_coverage | 20 | 인용 중 공식 비중 + 관할별 공식 도메인 매칭 |
| evidence_quality | 15 | 인용 도메인 티어 가중 평균(자기신고 미사용) |
| search_behavior | 10 | 쿼리당 고유 도메인 수율 + 쿼리 비중복률 |
| cross_validation | 10 | 교차검증 건수(3건 포화) × 다도메인 게이트 |
| gap_handling | 10 | 미검증 항목 명시 여부 |
| final_answer_structure | 5 | 인용 표기·구조·한계 명시(길이 무보상) |

### 채점 원칙 (수량 편향 제거)

- **카운트가 아니라 비율/품질**: 모든 지표는 [0,1] 비율이다. 쿼리·소스·답변을
  많이 만들어도 비율(앵커율·공식 비중·검색 수율)이 나쁘면 점수가 떨어진다.
  구버전은 `len(...)/N` 카운트 채점이라 물량공세가 무조건 이겼다.
- **자기신고 배제**: 근거 품질은 엔진이 로그에 적어온 `reliability_score`가 아니라
  결정론적 도메인 티어(backend `source_registry`와 동기화된 미러)로만 계산한다.
- **N/A 재정규화**: 로그 형식 때문에 측정 불가한 항목은 0점이 아니라 제외하고
  가용 가중치 기준 100점으로 재정규화한다(로그 형식이 순위를 좌우하는 편향 제거).
  리포트/JSON에서 `N/A`로 표기된다.
- **LLM 무관여**: 채점 전 과정이 순수 코드 계산이다.

### Pairwise 상대비교

절대 점수의 임계값 논쟁 대신, 같은 질의를 수행한 엔진 쌍끼리 직접 대조한다.
`comparison_raw_material.json`의 `pairwise`와 리포트 `## Pairwise` 섹션에:

- 항목별 승패(점수차가 만점의 10% 초과 시 승, 이하 무승부)
- 종합 승자(정규화 총점 차 3점 초과)
- 인용 도메인 자카드, **상대만 찾은 공식 도메인**(FinVision 개선의 직접 단서)

`comparison_raw_material.json`에는 FinVision 개선 원석도 포함됩니다(상대만 찾은
공식 도메인·관할, 갭 명시 누락, 공식쿼리 비중 열세, 저신뢰 인용 의존 등).
