# Gemini Financial Langfuse Log Runner

FinVision 서비스 코드와 분리된 실험용 러너입니다. Gemini Deep Research를 금융 리서치 특화 instruction prompt와 함께 실행하고, 관측 가능한 요청/응답/출처/오류 로그를 로컬 파일과 선택적 Langfuse trace로 저장합니다.

이 도구는 FinVision `backend/`, `frontend/`, DB를 수정하지 않습니다. OpenAI/ChatGPT API를 호출하지 않습니다. 모델 내부의 비공개 chain-of-thought를 요청하거나 복원하지 않습니다.

## 할 수 있는 일

- Gemini API 키만 있으면 로컬 HTML 로그창 생성
- Langfuse 키가 있으면 trace/span 업로드
- HTML 로그창 또는 Langfuse UI에서 사람이 직접 드래그 복사
- 금융 특화 instruction과 사용자 질문 prompt를 분리 관리

## 설치

```bash
cd research_lab/gemini_langfuse_log_runner
pip install -r requirements.txt
copy .env.example .env
```

`.env`에 Gemini 키를 넣습니다.

```env
GEMINI_API_KEY=
GEMINI_DEEP_RESEARCH_MODEL=deep-research-pro-preview-12-2025

LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

Langfuse 키는 선택입니다. Gemini 키만 있어도 로컬 HTML 로그창은 생성됩니다.

## 실행

repo 루트에서 실행:

```bash
python research_lab/gemini_langfuse_log_runner/run_gemini_deep_research.py --instruction research_lab/gemini_langfuse_log_runner/prompts/financial_research_instruction.txt --prompt research_lab/gemini_langfuse_log_runner/prompts/user_questions/indi_wuxi.txt
```

runner 폴더에서 실행:

```bash
cd research_lab/gemini_langfuse_log_runner
python run_gemini_deep_research.py --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/indi_wuxi.txt
```

Langfuse 없이 실행:

```bash
python run_gemini_deep_research.py --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/indi_wuxi.txt --no-langfuse
```

모델명 직접 지정:

```bash
python run_gemini_deep_research.py --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/nvda_china_export.txt --model deep-research-pro-preview-12-2025
```

## 출력 파일

```text
output/gemini_run_raw.json
output/gemini_run_record.json
output/gemini_run_summary.md
output/gemini_run_log_viewer.html
```

## HTML 로그창 사용법

1. `output/gemini_run_log_viewer.html` 파일을 브라우저로 엽니다.
2. 원하는 섹션을 드래그해서 복사합니다.
3. ChatGPT 또는 비교 도구에 붙여넣습니다.

Copy All 버튼도 있습니다. 브라우저 보안 설정 때문에 버튼 복사가 실패하면 직접 드래그해서 복사하면 됩니다.

## 질문 바꾸기

`prompts/user_questions/` 안에 새 `.txt` 파일을 만들거나 기존 파일을 수정합니다.

```bash
python run_gemini_deep_research.py --instruction prompts/financial_research_instruction.txt --prompt prompts/user_questions/aapl_india_supply_chain.txt --no-langfuse
```

## 주의

- 실제 API 키를 코드나 샘플 파일에 넣지 마세요.
- Gemini SDK/모델명 변경으로 실행이 실패할 수 있습니다. 이 경우 에러도 `output/`에 기록됩니다.
- Google Search grounding은 SDK가 지원하면 요청합니다. 모델이나 계정에서 지원하지 않으면 grounding 없이 한 번 재시도합니다.
- 이 도구는 OpenAI/ChatGPT API 실행 코드를 포함하지 않습니다.

