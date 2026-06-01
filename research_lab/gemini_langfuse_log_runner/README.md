# Gemini Financial Langfuse Log Runner

FinVision 서비스 코드와 분리된 실험용 러너입니다. Gemini를 금융 리서치 특화 instruction prompt와 함께 실행하고, 관측 가능한 요청/응답/출처/citation/오류/최종 답변을 로컬 파일과 선택적 Langfuse trace로 저장합니다.

이 도구는 FinVision `backend/`, `frontend/`, DB를 수정하지 않습니다. OpenAI/ChatGPT API를 호출하지 않습니다. 모델 내부의 비공개 chain-of-thought를 요청하거나 복원하지 않습니다.

## Modes

| mode | API | 설명 |
| --- | --- | --- |
| `grounded` | `generate_content` + Google Search grounding | 빠른 금융 리서치용, Deep Research Agent 아님 |
| `deep-research` | Interactions API Deep Research Agent | 진짜 Gemini Deep Research Agent |

`grounded` mode는 기존 generate_content 기반 실행입니다. 유용하지만 Gemini Deep Research Agent가 아닙니다.

`deep-research` mode는 Google Interactions API의 Deep Research Agent를 사용합니다. 기본 agent는 `deep-research-preview-04-2026`입니다.

## 설치

```bash
cd research_lab/gemini_langfuse_log_runner
pip install -r requirements.txt
copy .env.example .env
```

`.env`:

```env
GEMINI_API_KEY=
GEMINI_GROUNDED_MODEL=gemini-2.5-pro
GEMINI_DEEP_RESEARCH_AGENT=deep-research-preview-04-2026

LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

Langfuse 키는 선택입니다. Gemini 키만 있어도 로컬 HTML 로그창은 생성됩니다.

## Grounded Mode

```bash
python run_gemini_deep_research.py --mode grounded --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/indi_wuxi.txt --no-langfuse
```

repo 루트에서 실행:

```bash
python research_lab/gemini_langfuse_log_runner/run_gemini_deep_research.py --mode grounded --instruction research_lab/gemini_langfuse_log_runner/prompts/financial_research_instruction.txt --prompt research_lab/gemini_langfuse_log_runner/prompts/user_questions/indi_wuxi.txt --no-langfuse
```

## Deep Research Mode

```bash
python run_gemini_deep_research.py --mode deep-research --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/indi_wuxi.txt --no-langfuse
```

Max agent:

```bash
python run_gemini_deep_research.py --mode deep-research --agent deep-research-max-preview-04-2026 --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/indi_wuxi.txt
```

Polling 옵션:

```bash
python run_gemini_deep_research.py --mode deep-research --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/nvda_china_export.txt --poll-interval 10 --timeout 3600 --no-langfuse
```

SDK가 `client.interactions`를 제공하지 않으면 REST fallback을 시도합니다. 둘 다 실패하면 명확한 에러를 output 파일에 기록합니다. 이 경우 `--mode grounded`는 계속 사용할 수 있습니다.

## Langfuse 사용

`.env`에 Langfuse 키를 넣고 `--no-langfuse`를 빼면 됩니다.

```bash
python run_gemini_deep_research.py --mode deep-research --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/indi_wuxi.txt
```

Langfuse span:

- `mode_explanation`
- `financial_instruction`
- `user_prompt`
- `request_metadata`
- `polling_events`
- `citations`
- `final_answer`
- `raw_response_preview`
- `error_if_any`

## 출력 파일

```text
output/gemini_run_raw.json
output/gemini_run_record.json
output/gemini_run_summary.md
output/gemini_run_log_viewer.html
```

## HTML 로그창 사용법

1. `output/gemini_run_log_viewer.html` 파일을 브라우저로 엽니다.
2. 필요한 영역을 직접 드래그해서 복사합니다.
3. 비교 도구나 다른 AI 대화창에 붙여넣습니다.

Copy All 버튼도 있습니다. 브라우저 보안 설정 때문에 버튼 복사가 실패하면 직접 드래그해서 복사하면 됩니다.

## 질문 바꾸기

`prompts/user_questions/` 안에 새 `.txt` 파일을 만들거나 기존 파일을 수정합니다.

```bash
python run_gemini_deep_research.py --mode grounded --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/aapl_india_supply_chain.txt --no-langfuse
```

## 주의

- 실제 API 키를 코드나 샘플 파일에 넣지 마세요.
- Gemini SDK/agent/API revision 변경으로 실행이 실패할 수 있습니다. 이 경우 에러도 `output/`에 기록됩니다.
- Deep Research Agent는 Interactions API 기반입니다.
- `grounded` mode는 generate_content + Google Search grounding 기반이며 Deep Research Agent가 아닙니다.
- 이 도구는 OpenAI/ChatGPT API 실행 코드를 포함하지 않습니다.
