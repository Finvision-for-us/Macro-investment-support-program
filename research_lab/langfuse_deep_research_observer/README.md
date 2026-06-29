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

| 항목 | 점수 |
| --- | ---: |
| jurisdiction_detection | 15 |
| query_generation | 15 |
| official_source_coverage | 20 |
| evidence_quality | 15 |
| search_behavior | 10 |
| cross_validation | 10 |
| gap_handling | 10 |
| final_answer_structure | 5 |

`comparison_raw_material.json`에는 FinVision 개선 원석도 포함됩니다.
