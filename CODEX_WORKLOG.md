# Codex Worklog

Codex 작업 기록. Claude Code도 이 파일을 먼저 보면 된다.

## 기록 규칙
- 파일 수정, 구조 변경, 검증 실행 후 여기에 남긴다.
- 형식: 날짜 / 목적 / 수정 파일과 위치 / 이유 / 검증 / 남은 리스크.
- API 키 문자열 금지. 키 상태는 `has_key=True/False`만.
- 커밋 전 파일 선별 기준도 남긴다.

---

## 2026-07-06 - Deep Research F단계 모델 라우팅 및 E2E 검증

### 목적
- Gemini 2.5 Pro 무료 티어 `0/0` 문제 때문에 `pipeline.run()`이 synthesizer 단계에서 막히는 문제 해결.
- F단계 Discovery 엔진이 실제 파이프라인 안에서 끝까지 도는지 확인.
- 기본 검증 모델은 사용자 지시대로 `gemini-3.1-flash-lite`.

### 수정 파일

#### `backend/app/deep_research/config.py`
- 위치: LLM 설정 블록 상단.
- 변경:
  - `GEMINI_DEFAULT_MODEL = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-3.1-flash-lite")` 추가.
  - 기존 호환 alias 유지:
    - `GEMINI_LITE_MODEL`
    - `GEMINI_FLASH_MODEL`
    - `GEMINI_PRO_MODEL`
  - Deep Research 역할별 env 추가:
    - `DEEP_RESEARCH_PLANNER_MODEL`
    - `DEEP_RESEARCH_CRITIC_MODEL`
    - `DEEP_RESEARCH_SYNTH_MODEL`
    - `DEEP_RESEARCH_EXTRACT_MODEL`
    - `DEEP_RESEARCH_VERIFY_MODEL`
- 이유:
  - 무료 검증은 전부 `gemini-3.1-flash-lite`로 돌린다.
  - 유료 전환 시 코드 수정 없이 env만 바꿀 수 있게 한다.
  - 기존 `GEMINI_*_MODEL` 참조 코드와 호환 유지.

#### `backend/app/deep_research/agents/planner.py`
- 위치: config import, `_get_model()`.
- 변경:
  - `GEMINI_LITE_MODEL` 대신 `DEEP_RESEARCH_PLANNER_MODEL` 사용.
  - 로그를 특정 Lite 이름이 아니라 역할별 모델명으로 출력.
- 이유:
  - planner도 무료 검증 모델로 강제 라우팅.

#### `backend/app/deep_research/agents/critic.py`
- 위치: config import, `_get_model()`, fallback, grounding model 지정.
- 변경:
  - `GEMINI_FLASH_MODEL/GEMINI_LITE_MODEL` 대신 `DEEP_RESEARCH_CRITIC_MODEL/DEEP_RESEARCH_VERIFY_MODEL` 사용.
  - quota/429 발생 시 verify 모델 fallback 사용.
  - grounding의 `model=`도 `DEEP_RESEARCH_CRITIC_MODEL` 사용.
- 이유:
  - critic이 2.5-flash/pro 계열로 새지 않게 막음.
  - fallback도 무료 검증 모델로 유지.

#### `backend/app/deep_research/agents/synthesizer.py`
- 위치: config import, `_get_model()`, metadata extract model, verify model, narrative prompt.
- 변경:
  - `GEMINI_PRO_MODEL/GEMINI_FLASH_MODEL` 대신:
    - `DEEP_RESEARCH_SYNTH_MODEL`
    - `DEEP_RESEARCH_EXTRACT_MODEL`
    - `DEEP_RESEARCH_VERIFY_MODEL`
  - `_get_flash_model()`을 `_get_extract_model()` 의미로 정리.
  - `_get_verify_model(fallback=False)` 추가.
  - prompt 문구의 `## {섹션 제목}`을 `## {{섹션 제목}}`로 escape.
  - 최종 보고서 필수 섹션에 `## 미검증·불확실 항목` 요구 유지/강화.
- 이유:
  - synthesizer가 2.5-pro로 가는 경로 제거.
  - Python `.format()`이 `{섹션 제목}`을 변수로 오인해 `KeyError: '섹션 제목'` 발생. E2E 완주 막던 직접 원인.
  - 미검증 gap을 구조화 응답에 안정적으로 싣기 위함.

#### `backend/app/deep_research/pipeline.py`
- 위치: 최종 합성 progress message.
- 변경:
  - `"최종 보고서 작성 중 (Gemini Pro)..."` -> `"최종 보고서 작성 중 (Gemini)..."`
- 이유:
  - 실제 기본 모델이 Pro가 아니므로 UI/로그 오해 제거.

### 검증

#### import / 라우팅 확인
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -c "from app.deep_research.config import GEMINI_DEFAULT_MODEL, DEEP_RESEARCH_PLANNER_MODEL, DEEP_RESEARCH_CRITIC_MODEL, DEEP_RESEARCH_SYNTH_MODEL, DEEP_RESEARCH_EXTRACT_MODEL, DEEP_RESEARCH_VERIFY_MODEL; print({'default': GEMINI_DEFAULT_MODEL, 'planner': DEEP_RESEARCH_PLANNER_MODEL, 'critic': DEEP_RESEARCH_CRITIC_MODEL, 'synth': DEEP_RESEARCH_SYNTH_MODEL, 'extract': DEEP_RESEARCH_EXTRACT_MODEL, 'verify': DEEP_RESEARCH_VERIFY_MODEL})"
```
결과:
```text
{'default': 'gemini-3.1-flash-lite', 'planner': 'gemini-3.1-flash-lite', 'critic': 'gemini-3.1-flash-lite', 'synth': 'gemini-3.1-flash-lite', 'extract': 'gemini-3.1-flash-lite', 'verify': 'gemini-3.1-flash-lite'}
```

#### py_compile
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -m py_compile backend/app/deep_research/config.py backend/app/deep_research/agents/planner.py backend/app/deep_research/agents/critic.py backend/app/deep_research/agents/synthesizer.py backend/app/deep_research/pipeline.py
```
결과:
```text
OK
```

#### 실제 planner API 호출
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -c "exec('import asyncio\nfrom app.deep_research.agents.planner import Planner\nasync def main():\n    p=Planner()\n    plan=await p.plan(\'AAPL latest earnings\', {\'ticker\':\'AAPL\'})\n    print({\'sub_queries\': len(plan.sub_queries), \'model_tokens\': p.tokens_used, \'first\': plan.sub_queries[0].query if plan.sub_queries else None})\nasyncio.run(main())')"
```
결과:
```text
{'sub_queries': 10, 'model_tokens': 929, 'first': 'AAPL latest quarterly earnings report summary'}
```

#### 실제 pipeline.run 짧은 E2E
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend MAX_SEARCH_QUERIES_PER_RUN=2 DISCOVERY_MAX_SEARCHES=1 DISCOVERY_BREADTH=1 DISCOVERY_MAX_DEPTH=0 MAX_ITERATIONS=1 python3 -B -c "exec('import asyncio, uuid\nfrom app.deep_research.pipeline import DeepResearchPipeline\nfrom app.deep_research.models import DeepResearchRequest\nasync def main():\n    p=DeepResearchPipeline()\n    r=await p.run(DeepResearchRequest(query=\'AAPL latest earnings\', max_iterations=1, max_sources=1), str(uuid.uuid4()))\n    print({\'status\': r.status.value, \'error\': r.error, \'sources\': len(r.sources), \'queries\': r.metadata.total_queries, \'discovery_leads\': r.metadata.discovery_leads, \'recovered\': r.metadata.recovered_sources, \'gaps\': len(r.unverified_gaps), \'sections\': len(r.sections), \'summary_len\': len(r.summary or \'\')})\nasyncio.run(main())')"
```
결과:
```text
{'status': 'done', 'error': None, 'sources': 9, 'queries': 2, 'discovery_leads': 1, 'recovered': 0, 'gaps': 5, 'sections': 7, 'summary_len': 255}
```

### 못 한 것
- `pytest` 실행 못 함.
- 이유:
  - 시스템 Python: `No module named pytest`
  - `backend/venv`: `pytest` 없음, `dotenv`도 없음.

### 관찰
- 샌드박스 안 네트워크 호출은 오래 멈췄다.
- 승인된 네트워크 실행에서는 planner와 pipeline E2E 성공.
- `google.generativeai` 구 SDK deprecation warning 출력됨. 현재 프로젝트 지시상 구 SDK 유지.
- E2E 중 SEC 쪽 500 retry 로그 있었지만 pipeline은 계속 진행했고 결과 `error=None`.

### 남은 다음 단계
1. `backend/app/deep_research/discovery/lead_follower.py` 단서 우선순위 튜닝.
2. INDI/Wuxi 같은 난제 쿼리로 Discovery 큰 E2E 검증.
3. `unverified_gaps` 프론트 렌더.
4. observer 재측정.
5. Exa/Brave/HKEX/Cninfo/EDINET/IR 소스 확장.

### 커밋 주의
- 아직 커밋 안 함.
- `git add .` 금지.
- `.err`, handoff, `__pycache__` 제외.
- 명시 파일만 stage.

---

## 2026-07-06 - Discovery lead_follower 단서 우선순위 튜닝

### 목적
- F단계 다음 작업 1번.
- `lead_follower`가 재무·공시 단서보다 케이스스터디/채용/헤드헌터 같은 메타 단서로 흐르는 문제 완화.
- 단서는 수집 텍스트/목표에 실제 등장한 것만 쓰는 무할루시네이션 원칙 유지.

### 수정 파일

#### `backend/app/deep_research/discovery/lead_follower.py`
- 위치: `LEAD_PROMPT`.
- 변경:
  - 높은 우선순위 명시:
    - 공식 공시·원문 문서
    - 재무 실질 단서
    - 거래·소유권 단서
    - 사업 영향 단서
    - 규제·관할 단서
  - 낮은 우선순위 명시:
    - 채용/헤드헌터/HR
    - SEO 글
    - 일반 케이스스터디
    - 컨설팅 홍보
    - 팟캐스트/행사 소개
    - 추상적 시장전망
  - 단서 형식 선호 추가:
    - `회사/티커 + 사건/문서/수치/관할`
- 이유:
  - 모델이 “더 찾아볼 만한 것”을 넓게 해석해 금융 실질보다 메타 문서로 확장하는 경향이 있었음.

- 위치: prompt 아래 상수.
- 변경:
  - `_HIGH_VALUE_LEAD_KEYWORDS` 추가.
  - `_LOW_VALUE_LEAD_KEYWORDS` 추가.
  - `_GROUNDING_STOPWORDS` 추가.
- 이유:
  - 모델 출력 후 deterministic ranking/filtering으로 금융 단서 우선순위를 보장.

- 위치: `LeadFollower._extract_leads()`.
- 변경:
  - 주입 extractor 결과와 Gemini 결과 모두 `_rank_financial_leads(..., grounding_text=f"{goal}\n{text}")`를 거치게 함.
- 이유:
  - 테스트/실사용 모두 같은 우선순위 규칙 적용.

- 위치: 파일 하단 helper.
- 변경:
  - `_rank_financial_leads()` 추가/확장.
  - `_is_grounded_lead()` 추가.
  - `_strip_ungrounded_tokens()` 추가.
- 이유:
  - 모델이 `exhibit`, `guidance`, `entity contractual obligations`처럼 수집 텍스트에 없는 단어를 붙이는 것을 확인.
  - 미근거 단어가 있으면 단서 전체를 버리지 않고, 근거 있는 토큰만 남겨 축약.
  - 순수 HR/마케팅/케이스스터디 단서는 대체할 실질 단서가 있을 때 제외.

### 검증

#### py_compile
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -m py_compile backend/app/deep_research/discovery/lead_follower.py
```
결과:
```text
OK
```

#### ranking/filter 단위 확인
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -c "from app.deep_research.discovery.lead_follower import _rank_financial_leads; text='indie Semiconductor INDI SEC 8-K Wuxi stake sale revenue impact customer contracts recruiter case study'; leads=['INDI SEC 8-K Wuxi stake sale exhibit','indie Semiconductor Wuxi divestiture financial impact guidance','INDI Wuxi revenue impact','recruiter case study']; print(_rank_financial_leads(leads, set(), 3, text))"
```
결과:
```text
['INDI SEC 8-K Wuxi stake sale', 'INDI Wuxi revenue impact', 'indie Semiconductor Wuxi impact']
```

#### BFS 목 검증
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -c "exec('import asyncio\nfrom app.deep_research.discovery.lead_follower import LeadFollower\nfrom app.deep_research.models import SearchResult\nasync def search(q):\n    return [SearchResult(url=\'https://example.com/\'+q.replace(\' \',\'-\'), title=q, content=\'INDI SEC 8-K Wuxi stake sale revenue impact customer contracts recruiter case study\', source_type=\'mock\', relevance_score=1.0)]\nasync def extract(text, goal, visited):\n    return [\'recruiter case study\', \'INDI SEC 8-K Wuxi stake sale exhibit\', \'INDI Wuxi revenue impact\']\nasync def main():\n    lf=LeadFollower(); lf.set_search(search); lf.set_lead_extractor(extract)\n    r=await lf.deepen(\'INDI Wuxi stake sale\', max_depth=1, breadth=2, max_searches=3)\n    print({\'searches\': r.searches, \'explored\': r.explored, \'edges\': r.edges})\nasyncio.run(main())')"
```
결과:
```text
{'searches': 3, 'explored': [{'lead': 'INDI Wuxi stake sale', 'depth': 0}, {'lead': 'INDI SEC 8-K Wuxi stake sale', 'depth': 1}, {'lead': 'INDI Wuxi revenue impact', 'depth': 1}], 'edges': [{'parent': 'INDI Wuxi stake sale', 'child': 'INDI SEC 8-K Wuxi stake sale', 'depth': 1}, {'parent': 'INDI Wuxi stake sale', 'child': 'INDI Wuxi revenue impact', 'depth': 1}]}
```

#### 실제 Gemini 3.1 flash-lite 단서추출 확인
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend python3 -B -c "exec('import asyncio\nfrom app.deep_research.discovery.lead_follower import LeadFollower\nasync def main():\n    text=\'indie Semiconductor disclosed an INDI SEC 8-K about Wuxi stake sale. The article mentions possible revenue impact, margin impact, customer contracts, and a recruiter case study.\'\n    lf=LeadFollower()\n    leads=await lf._extract_leads(text, \'INDI Wuxi stake sale financial impact\', set(), k=3)\n    print(leads)\nasyncio.run(main())')"
```
결과:
```text
['INDI SEC 8-K Wuxi stake sale', 'indie Semiconductor Wuxi revenue margin impact', 'indie Semiconductor Wuxi financial']
```

### 관찰
- 첫 실제 Gemini 호출에서는 `exhibit`, `guidance`, `entity contractual obligations` 같은 미근거 단어가 붙었다.
- 그래서 prompt만이 아니라 후처리 grounding filter가 필요했다.
- 보강 후에는 recruiter/case study 대신 SEC 8-K, revenue/margin impact 쪽으로 정렬됨.
- 구 SDK deprecation warning은 계속 출력됨. 현재 프로젝트 지시상 구 SDK 유지.

### 남은 다음 단계
1. INDI/Wuxi 같은 난제 쿼리로 Discovery 큰 E2E 검증.
2. `unverified_gaps` 프론트 렌더.
3. observer 재측정.

---

## 2026-07-06 - INDI/Wuxi Discovery E2E 검증

### 목적
- F단계 다음 작업 2번.
- 튜닝한 `lead_follower`가 실제 파이프라인에서 INDI/Wuxi 난제 쿼리를 처리하는지 확인.
- 목표:
  - `pipeline.run()` 완주.
  - Discovery가 실제로 단서 확장.
  - SEC/중국 거래소/규제 출처가 결과에 들어오는지 확인.
  - `unverified_gaps`가 생성되는지 확인.

### 검증 1차
명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend MAX_SEARCH_QUERIES_PER_RUN=8 DISCOVERY_MAX_SEARCHES=4 DISCOVERY_BREADTH=2 DISCOVERY_MAX_DEPTH=1 MAX_ITERATIONS=1 MAX_RUN_SECONDS=240 python3 -B -c "exec('import asyncio, json, uuid\nfrom app.deep_research.pipeline import DeepResearchPipeline\nfrom app.deep_research.models import DeepResearchRequest\nasync def main():\n    p=DeepResearchPipeline()\n    req=DeepResearchRequest(query=\'INDI Wuxi stake sale financial impact\', context={\'ticker\': \'INDI\', \'company\': \'indie Semiconductor\'}, max_iterations=1, max_sources=6)\n    r=await p.run(req, str(uuid.uuid4()))\n    data={\n        \'status\': r.status.value,\n        \'error\': r.error,\n        \'sources\': len(r.sources),\n        \'queries\': r.metadata.total_queries,\n        \'discovery_leads\': r.metadata.discovery_leads,\n        \'recovered\': r.metadata.recovered_sources,\n        \'gaps\': len(r.unverified_gaps),\n        \'sections\': len(r.sections),\n        \'summary_len\': len(r.summary or \'\'),\n        \'sample_gaps\': r.unverified_gaps[:3],\n        \'sample_sections\': [s.title for s in r.sections[:5]],\n        \'sample_sources\': [s.domain for s in r.sources[:8]],\n    }\n    print(json.dumps(data, ensure_ascii=False, indent=2))\nasyncio.run(main())')"
```
결과:
```json
{
  "status": "done",
  "error": null,
  "sources": 116,
  "queries": 9,
  "discovery_leads": 1,
  "recovered": 0,
  "gaps": 4,
  "sections": 6,
  "summary_len": 519,
  "sample_gaps": [
    "United Faith의 주주 승인 절차의 구체적인 일정 및 완료 여부.",
    "규제 당국 승인 과정에서 발생할 수 있는 잠재적 이슈나 지연 가능성.",
    "매각 자금($135 million)의 구체적인 활용처(부채 상환, R&D 투자, 운영 자금 등)에 대한 세부 계획."
  ],
  "sample_sections": [
    "Financial Impact Analysis (Balance Sheet & Cash Flow)",
    "Strategic Rationalization for Divestiture",
    "Regulatory & Jurisdictional Considerations",
    "Market Sentiment & Analyst Perspectives",
    "Future Outlook & Risk Assessment"
  ],
  "sample_sources": [
    "sec.gov",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "csrc.gov.cn",
    "disc.static.szse.cn"
  ]
}
```
판단:
- E2E는 성공.
- 그러나 `discovery_leads=1`.
- 초기 검색이 `MAX_SEARCH_QUERIES_PER_RUN=8` 예산을 먼저 소모해 Discovery가 seed 이상으로 충분히 확장하지 못함.

### 검증 2차
변경:
- `MAX_SEARCH_QUERIES_PER_RUN=40`으로 올림.
- 나머지는 제한 유지:
  - `DISCOVERY_MAX_SEARCHES=4`
  - `DISCOVERY_BREADTH=2`
  - `DISCOVERY_MAX_DEPTH=1`
  - `MAX_ITERATIONS=1`

명령:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=backend MAX_SEARCH_QUERIES_PER_RUN=40 DISCOVERY_MAX_SEARCHES=4 DISCOVERY_BREADTH=2 DISCOVERY_MAX_DEPTH=1 MAX_ITERATIONS=1 MAX_RUN_SECONDS=300 python3 -B -c "exec('import asyncio, json, uuid\nfrom app.deep_research.pipeline import DeepResearchPipeline\nfrom app.deep_research.models import DeepResearchRequest\nasync def main():\n    p=DeepResearchPipeline()\n    req=DeepResearchRequest(query=\'INDI Wuxi stake sale financial impact\', context={\'ticker\': \'INDI\', \'company\': \'indie Semiconductor\'}, max_iterations=1, max_sources=4)\n    r=await p.run(req, str(uuid.uuid4()))\n    data={\n        \'status\': r.status.value,\n        \'error\': r.error,\n        \'sources\': len(r.sources),\n        \'queries\': r.metadata.total_queries,\n        \'discovery_leads\': r.metadata.discovery_leads,\n        \'recovered\': r.metadata.recovered_sources,\n        \'gaps\': len(r.unverified_gaps),\n        \'sections\': len(r.sections),\n        \'summary_len\': len(r.summary or \'\'),\n        \'sample_gaps\': r.unverified_gaps[:4],\n        \'sample_sections\': [s.title for s in r.sections[:6]],\n        \'sample_sources\': [s.domain for s in r.sources[:12]],\n    }\n    print(json.dumps(data, ensure_ascii=False, indent=2))\nasyncio.run(main())')"
```
결과:
```json
{
  "status": "done",
  "error": null,
  "sources": 150,
  "queries": 16,
  "discovery_leads": 3,
  "recovered": 0,
  "gaps": 3,
  "sections": 6,
  "summary_len": 509,
  "sample_gaps": [
    "United Faith의 주주 승인 완료 여부와 최종 규제 당국의 승인 시점은 구체적으로 명시되지 않음.",
    "매각 대금 입금과 관련하여 2026년 말 완료 시점 외에 분할 지급 여부 등 상세 지급 일정은 공개되지 않음.",
    "해당 거래가 indie의 2026년 연간 영업이익률에 미치는 구체적인 수치적 영향은 추후 재무 공시를 통한 확인이 필요함."
  ],
  "sample_sections": [
    "Financial Implications for INDI",
    "Strategic Pivot and Operational Impact",
    "Regulatory and Market Context in China",
    "Investor Sentiment and Market Reaction",
    "Future Outlook for indie Semiconductor",
    "미검증·불확실 항목"
  ],
  "sample_sources": [
    "sec.gov",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "static.sse.com.cn",
    "disc.static.szse.cn",
    "disc.static.szse.cn",
    "static.sse.com.cn",
    "disc.static.szse.cn",
    "static.sse.com.cn",
    "iconnect007.com"
  ]
}
```

### 판단
- 성공.
- `pipeline.run()` 완주: `status=done`, `error=null`.
- Discovery 확장 확인: `discovery_leads=3`.
- 1차 공개자료/관할 출처 확인:
  - `sec.gov`
  - `static.sse.com.cn`
  - `disc.static.szse.cn`
  - `csrc.gov.cn`는 1차 실행에서 확인.
- `미검증·불확실 항목` 섹션 생성 확인.
- `unverified_gaps` 생성 확인.

### 관찰
- `recovered=0`.
  - 이번 쿼리에서는 게이트/죽은 URL 복구 경로가 실제로 작동할 필요가 없었거나, 복구 성공 건이 없었음.
  - Wayback/alternate recovery 검증은 별도 케이스가 필요.
- 2차 실행 중 synthesizer 검증 로그:
  - `미검증 수치: ['1억', '500만']`
  - source matcher가 일부 수치 검증 실패를 감지함.
  - 이건 방어선이 작동한 신호. 해당 수치가 최종 결과에서 어떻게 표시됐는지는 별도 상세 확인 필요.
- 검색 예산이 낮으면 Discovery가 seed만 탐색하고 끝난다.
  - 실사용 기본값 `MAX_SEARCH_QUERIES_PER_RUN=150`이면 이번 문제는 덜할 가능성 높음.
  - 다만 초기 planner 쿼리가 많은 구조라 Discovery 전용 예산 분리가 앞으로 필요할 수 있음.

### 남은 다음 단계
1. `unverified_gaps` 프론트 렌더.
2. observer 재측정.
3. Wayback/alternate recovery를 실제 E2E에서 따로 검증.

---

## 2026-07-06 - Deep Research unverified_gaps 프론트 렌더

### 목적
- F단계 다음 작업 3번.
- 백엔드 `DeepResearchResponse.unverified_gaps`를 사용자 화면에 노출.
- 딥리서치 보고서가 “확인 못 한 것”을 숨기지 않게 함.

### 수정 파일

#### `frontend/src/components/shared/StockResearchChat.jsx`
- 위치: lucide-react import.
- 변경:
  - `AlertTriangle` 아이콘 추가.
- 이유:
  - 미검증·불확실 항목 섹션의 시각적 신호.

- 위치: `CoverageSection` 아래.
- 변경:
  - `UnverifiedGapsSection({ gaps })` 추가.
  - `gaps`가 없으면 렌더하지 않음.
  - 접힘/펼침 UI.
  - amber 계열 border/background.
  - 항목 수 badge 표시.
- 이유:
  - 기존 `CoverageSection`과 같은 구조로 보고서 안에서 자연스럽게 확인 가능.
  - 기본은 접힘 처리로 보고서 가독성 유지.

- 위치: `ResearchReport` 내부, 출처 커버리지 아래/번호 각주 위.
- 변경:
  - `<UnverifiedGapsSection gaps={result.unverified_gaps} />` 추가.
- 이유:
  - 핵심 보고서/커버리지 확인 후, 참고 출처 목록 전에 미확인 항목을 보게 배치.

### 검증

#### 1차 빌드
명령:
```bash
cd frontend
npm run build
```
결과:
```text
Error: Cannot find module @rollup/rollup-linux-x64-gnu
```
원인:
- Rollup optional dependency 누락.
- 코드 변경 문제가 아니라 npm optional dependency 설치 상태 문제.

#### 의존성 복구
명령:
```bash
cd frontend
npm install
```
결과:
```text
added 3 packages
9 vulnerabilities (1 low, 3 moderate, 5 high)
```
조치:
- `npm audit fix`는 실행하지 않음. 범위 밖이고 breaking change 가능.
- `npm install` 후 `package.json/package-lock.json` 줄끝 변경이 생겼으나 LF로 정리했고 최종 diff 없음.

#### 2차 빌드
명령:
```bash
cd frontend
npm run build
```
결과:
```text
✓ built in 1m 53s
```
경고:
```text
NewsFeed.jsx dynamic/static import chunk warning
StockResearchChat.jsx dynamic/static import chunk warning
Some chunks are larger than 500 kB
```
판단:
- 기존 Vite chunk 경고. 빌드는 성공.

### 관찰
- 실제 화면 클릭 테스트는 아직 안 함.
- 빌드 기준 문법/번들링은 통과.
- `npm install` 후 `frontend/package.json`, `frontend/package-lock.json`이 `git status`에는 modified로 보이나 `git diff --quiet -- frontend/package.json frontend/package-lock.json` 결과는 `0`이라 내용 diff는 없음.
- `.git`가 read-only라 `git update-index --refresh`는 실패함. 커밋 시 package 파일은 내용 diff 확인 후 제외 권장.

### 남은 다음 단계
1. observer 재측정.
2. Wayback/alternate recovery E2E 별도 검증.

---

## 2026-07-06 - Observer 재측정

### 목적
- F단계 다음 작업 4번.
- 모델 라우팅, Discovery 단서 튜닝, `unverified_gaps` 출력 반영 후 FinVision 점수가 실제로 개선됐는지 확인.
- 기존 기준선: FinVision 65.88 / Gemini 89.48 / OpenAI 84.07.

### 방식
- 기존 샘플 FinVision 로그를 그대로 쓰지 않음.
- 현재 코드로 INDI/Wuxi 쿼리를 실제 실행해 `/tmp/finvision_observer_current.json` 생성.
- 기존 Gemini/OpenAI 샘플과 현재 FinVision JSON을 observer로 비교.
- repo output 파일은 덮어쓰지 않고 `/tmp/finvision_observer_current_output`에 생성.

### 실행 1 - 현재 FinVision observer 입력 생성
파일:
- 임시 스크립트: `/tmp/finvision_observer_current.py`
- 생성 JSON: `/tmp/finvision_observer_current.json`

환경:
```bash
PYTHONIOENCODING=utf-8
PYTHONPATH=backend
MAX_SEARCH_QUERIES_PER_RUN=40
DISCOVERY_MAX_SEARCHES=4
DISCOVERY_BREADTH=2
DISCOVERY_MAX_DEPTH=1
MAX_ITERATIONS=1
MAX_RUN_SECONDS=300
```

결과:
```json
{
  "path": "/tmp/finvision_observer_current.json",
  "status": "done",
  "sources": 143,
  "queries": 19,
  "discovery_leads": 3,
  "gaps": 2
}
```

관찰:
- Jina Reader가 SEC URL 하나에서 네트워크 재시도 후 실패:
  - `https://www.sec.gov/Archives/edgar/data/1841925/000121390022002883/fs12022a2_indiesemialpha.htm`
- 파이프라인은 계속 진행했고 최종 결과는 `status=done`.

### 실행 2 - observer 비교
명령:
```bash
PYTHONIOENCODING=utf-8 python3 -B research_lab/langfuse_deep_research_observer/run_compare.py \
  --gemini research_lab/langfuse_deep_research_observer/input/gemini_log_sample.txt \
  --openai research_lab/langfuse_deep_research_observer/input/openai_log_sample.json \
  --finvision /tmp/finvision_observer_current.json \
  --output-dir /tmp/finvision_observer_current_output
```

출력:
```text
Wrote comparison outputs to /tmp/finvision_observer_current_output
```

### 점수
| Engine | Total | Jurisdiction | Queries | Official Sources | Evidence | Search | Cross Check | Gaps | Answer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini | 89.48 | 15.0 | 15.0 | 18.0 | 12.5 | 7.83 | 6.67 | 10.0 | 4.48 |
| openai | 84.07 | 15.0 | 15.0 | 16.0 | 13.5 | 7.67 | 3.33 | 10.0 | 3.57 |
| finvision | 72.63 | 15.0 | 6.0 | 15.0 | 12.63 | 5.67 | 3.33 | 10.0 | 5.0 |

### 판단
- 개선 확인:
  - FinVision 65.88 -> 72.63.
  - +6.75점.
- 좋아진 부분:
  - 관할 감지 만점: 15.0.
  - gap handling 만점: 10.0.
  - final answer structure 만점: 5.0.
  - official source coverage 15.0까지 올라옴.
- 아직 약한 부분:
  - query_generation 6.0.
  - search_behavior 5.67.
  - cross_validation 3.33.

### Observer가 제안한 개선 원석
항목:
- `official_query_generation`

내용:
- Description: FinVision generated fewer official-source queries than the external research logs.
- Suggested fix: Generate more site-specific queries for regulators, exchanges, and issuer IR pages.
- Priority: medium.

### 결론
- 지금까지 작업은 점수 개선으로 확인됨.
- 다음으로 가장 효율적인 개선은 official-source query 생성 강화.
- 구체적으로 planner/official_source_searcher 쪽에서 관할별 `site:` 쿼리를 더 많이 생성해야 함.

### 남은 다음 단계
1. official-source query generation 강화.
2. Wayback/alternate recovery E2E 별도 검증.

---

## 2026-07-06 - Official-source query generation 강화

### 목적
- observer 약점인 `query_generation` / official-source query 생성 부족 개선.
- INDI/Wuxi 같은 cross-border 질의에서 공식 `site:` 쿼리가 회사/티커 없이 너무 넓게 나가는 문제 수정.
- 실행한 검색 쿼리를 응답 JSON/metadata에 남겨 observer가 실제 검색 행동을 볼 수 있게 함.

### 수정 파일

#### `backend/app/deep_research/agents/multilingual_query_builder.py`
- 위치: `_get_entity()` 앞/내부.
- 변경:
  - `context.company`, `context.name`, `context.longName`, `context.shortName` 지원.
  - `context.finvision_data/internal_context/overview` 문자열에서 `회사명:` 또는 `회사:` 라인을 추출.
- 이유:
  - 실제 UI 경로는 `finvision_data` 문자열만 가진 경우가 있어 회사명이 쿼리에 빠질 수 있었음.

- 위치: `_add_cn_queries()` CN official site-query 생성부.
- 변경:
  - 기존: `site:csrc.gov.cn 无锡 出售`
  - 변경: `site:csrc.gov.cn 英迪半导体 无锡 出售`
  - `sse.com.cn`, `szse.cn`, `hkexnews.hk`, `bse.com.cn`도 동일하게 회사/티커 앵커 포함.
- 이유:
  - 지명+이벤트만 있는 공식 쿼리는 검색 범위가 넓어져 무관 결과 위험이 컸음.

#### `backend/app/deep_research/sources/official_source_searcher.py`
- 위치: `OfficialSourceSearcher` tracking 필드/메서드.
- 변경:
  - `_last_query_strings` 추가.
  - `reset_tracking()` 추가.
  - `last_query_strings`, `last_query_count`, `last_searched_domains` property 추가.
- 이유:
  - 실제 실행한 공식 쿼리를 pipeline/observer에 전달하기 위함.
  - singleton stale state 방지.

- 위치: `search()` selected query 선택.
- 변경:
  - `site_queries[:6] + local_queries[:2]` -> `site_queries[:8] + local_queries[:2]`.
- 이유:
  - US+CN cross-border에서 HKEx 영문 쿼리 같은 후순위 공식 쿼리가 잘리는 문제 완화.

#### `backend/app/deep_research/models.py`
- 위치: `ResearchMetadata`.
- 변경:
  - `generated_queries`
  - `official_source_queries`
  - `searched_official_domains`
- 위치: `DeepResearchResponse`.
- 변경:
  - top-level `generated_queries`
  - top-level `official_source_queries`
- 이유:
  - 응답 JSON만으로 실제 검색 행동을 회귀평가 가능하게 함.

#### `backend/app/deep_research/pipeline.py`
- 위치: run 시작부.
- 변경:
  - 매 run마다 `official_source_searcher.reset_tracking()` 호출.
- 이유:
  - 이전 job의 공식 검색 tracking이 다음 job coverage/metadata에 섞이지 않게 함.

- 위치: planner 직후.
- 변경:
  - plan sub_queries를 `metadata.generated_queries`에 기록.
  - plan 안 `site:` 쿼리를 `metadata.official_source_queries`에 기록.
- 이유:
  - planner가 실제 생성한 검색 쿼리 보존.

- 위치: 공식 검색 직후.
- 변경:
  - official searcher가 실제 선택/실행한 query strings를 metadata에 병합.
  - searched official domains 기록.
- 이유:
  - deterministic official-source search도 observer에서 보이게 함.

- 위치: 최종 합성 전/후.
- 변경:
  - `metadata.total_queries = self.searcher.total_queries + official_source_searcher.last_query_count`.
  - response top-level `generated_queries`, `official_source_queries`에 복사.
- 이유:
  - 기존 total_queries는 official searcher 내부 검색을 누락했음.

### 검증

#### py_compile
명령:
```bash
PYTHONPATH=backend PYTHONIOENCODING=utf-8 python -B -m py_compile backend/app/deep_research/agents/multilingual_query_builder.py backend/app/deep_research/sources/official_source_searcher.py backend/app/deep_research/models.py backend/app/deep_research/pipeline.py
```
결과:
```text
OK
```

#### query builder 단위 확인 - context.company
명령:
```bash
PYTHONPATH=backend PYTHONIOENCODING=utf-8 python -B -c "from app.deep_research.agents.jurisdiction_detector import jurisdiction_detector; from app.deep_research.agents.multilingual_query_builder import multilingual_query_builder; q='INDI Wuxi stake sale financial impact'; ctx={'ticker':'INDI','company':'indie Semiconductor'}; j=jurisdiction_detector.detect(q, ctx); m=multilingual_query_builder.build(q,j,ctx); print({'primary':j.primary,'secondary':j.secondary,'cross':j.is_cross_border,'event':j.event_type,'count':len(m.queries)}); [print(f'{x.query_type}|{x.country}|{x.site_domain}|{x.query}') for x in m.queries]"
```
결과 핵심:
```text
count=11
site:sec.gov INDI 8-K divestiture
site:sec.gov "indie Semiconductor" sale
site:csrc.gov.cn 英迪半导体 无锡 出售
site:sse.com.cn 英迪半导体 无锡 出售
site:szse.cn 英迪半导体 无锡 出售
site:hkexnews.hk 英迪半导体 无锡 出售
site:bse.com.cn 英迪半导体 无锡 出售
site:hkexnews.hk indie Semiconductor sale
```

#### query builder 단위 확인 - finvision_data 문자열 회사명
명령:
```bash
PYTHONPATH=backend PYTHONIOENCODING=utf-8 python -B -c "from app.deep_research.agents.jurisdiction_detector import jurisdiction_detector; from app.deep_research.agents.multilingual_query_builder import multilingual_query_builder; q='INDI Wuxi stake sale financial impact'; ctx={'ticker':'INDI','finvision_data':'### 종목 개요\n회사: indie Semiconductor\n섹터: Technology'}; j=jurisdiction_detector.detect(q, ctx); m=multilingual_query_builder.build(q,j,ctx); [print(x.query) for x in m.queries if x.query_type=='official_site']"
```
결과:
```text
회사명 앵커 포함 확인. CN 공식 쿼리 모두 `英迪半导体 无锡 出售` 포함.
```

#### diff whitespace 검사
명령:
```bash
git diff --check
```
결과:
```text
OK. 단, 기존 파일들의 LF->CRLF warning 출력.
```

### 못 한 것
- `pipeline.run()` E2E와 observer 재측정은 이번 턴에서 못 함.
- 이유:
  - 현재 PowerShell 시스템 Python: `pydantic` 없음.
  - `backend/venv`는 WSL 구조지만 `pip`도 없고 `pydantic`도 없음.
  - 따라서 FastAPI/Pydantic 기반 import 실행 불가.

### 남은 리스크
- 실제 검색 API 환경에서 official query 수 증가가 Tavily/Parallel quota를 조금 더 쓸 수 있음.
- `site_queries[:8]`로 늘렸지만, 국가가 더 많은 cross-border 사건에서는 여전히 후순위 공식 쿼리가 잘릴 수 있음.
- 현재 회사 IR 도메인은 context에 URL/domain이 없으면 자동 생성하지 않음. 무근거 도메인 추측은 하지 않았음.
- E2E/observer 점수 상승은 의존성 설치된 환경에서 재측정 필요.

### 다음 단계
1. 의존성 있는 환경에서 INDI/Wuxi `pipeline.run()` 재실행.
2. observer 재측정.
3. 필요하면 planner prompt에도 공식 `site:` query 최소 개수 규칙 추가.
4. 회사 IR URL이 overview/context에 들어오도록 별도 설계.

---

## 2026-07-06 - (Claude Code) Codex 작업 로컬 검증: E2E + observer 재측정

### 목적
- Codex가 의존성 부재로 못 한 최종 검증(전체 pipeline.run E2E + observer 재측정)을 pydantic/검색키 있는 환경에서 수행.
- Codex 코드 품질을 실제 코드로 평가.

### 코드 평가 (읽고 판단)
- 모델 라우팅(config.py): 역할별 env 분리. 양호, 단일 default보다 정교.
- lead_follower grounding 필터(`_is_grounded_lead`/`_strip_ungrounded_tokens`): 미근거 토큰만 제거. 무할루시네이션에 견고. 유지 권장.
- official_source_searcher tracking / multilingual_query_builder 회사 앵커: 깔끔. 유지 권장.
- 결론: Codex 방향 = 기존 방향과 동일, 실행 품질 양호. 되돌릴 것 없음.

### 검증
- py_compile: 전체(config/planner/critic/synthesizer/query_builder/official_searcher/models/pipeline/discovery 3) → OK.
- import DeepResearchPipeline → OK, DEEP_RESEARCH_SYNTH_MODEL=gemini-3.1-flash-lite.
- E2E (INDI/Wuxi, MAX_SEARCH_QUERIES_PER_RUN=40 DISCOVERY_MAX_SEARCHES=4 BREADTH=2 DEPTH=1 MAX_ITERATIONS=1):
  - status=done, error=None
  - sources=158, total_queries=26
  - generated_queries=18, official_source_queries=10 (회사앵커 `英迪半导体` 포함 확인)
  - discovery_leads=3, gaps=3, sections=4
  - 출처: sec.gov(다수), static.sse.com.cn, disc.static.szse.cn, gurufocus, finance.yahoo
- observer 재측정 (현재 코드로 생성한 finvision JSON vs 기존 gemini/openai 샘플):
  - FinVision Total 73.67 (기준선 65.88, Codex 기록 72.63)
  - **query_generation 6.0 → 15.0 (만점)** — Codex official-query 강화 효과 실증.
  - Jurisdiction 15, Gaps 10, Answer 5 유지.
  - Official Sources 10.0 (실행별 편차), Search 6.67.
  - **cross_validation 0.0** — 단, 이번 observer 입력 JSON에 `cross_source_consistency`를 안 실어서 0. 파이프라인 cross_checker 결과가 응답 필드로 노출 안 되는 구조 문제. 아티팩트 + 실제 약점 둘 다.

### 판단
- Codex 전체 체인 E2E 완주 확인(Codex가 못 한 부분).
- query_generation 개선은 실측 확정.
- 다음 최대 약점 = cross_validation. 두 가지 필요:
  (a) 파이프라인 cross_checker/critic의 교차검증 결과를 DeepResearchResponse 필드(cross_source_consistency 등)로 노출.
  (b) observer 입력 생성 시 그 필드 포함.

### 남은 리스크
- observer 점수는 입력 JSON 구성에 민감 → 표준 observer-입력 생성기(파이프라인 응답→observer 스키마)를 만들어 재현성 확보 필요.
- 커밋 아직 안 함. 명시 파일만 stage 예정(.err/handoff/package-lock 제외 검토).

### 다음 단계
1. cross_validation: 파이프라인이 교차검증 근거를 응답에 노출 + 강화.
2. 표준 observer-입력 생성기 작성(재현성).
3. Wayback/alternate recovery 별도 케이스 E2E.

---

## 2026-07-06 - (Claude Code) cross_validation 노출

### 목적
- observer 최대 약점 cross_validation(0~3.33) 개선.
- 근본원인: `agents/cross_checker.py`(MultiSourceCrossChecker)가 synthesizer에 import만 되고 **어디서도 호출 안 됨(죽은 코드)**. 결과도 응답에 없음.

### 수정 파일
- `backend/app/deep_research/models.py`: `DeepResearchResponse.cross_validation: list[str]` 필드 추가.
- `backend/app/deep_research/agents/synthesizer.py`:
  - `_cross_validate(key_findings, raw_storage)` 추가 — key finding별로 `cross_checker.cross_check()`를 실제 실행, 결과를 문장화(일치 N출처 / 수치 상충 / 단일출처 미교차 / 교차 근거 부족).
  - synthesize()에서 호출 + 응답 `cross_validation`에 실음.
- 새 사실 생성 안 함(무할루시네이션). 각 주장의 교차검증 결과만 기록.

### 검증
- py_compile OK.
- 단위: 목 RawSourceStorage로 `_cross_validate` 문장 생성/빈입력 처리 확인.
- E2E(INDI/Wuxi): status=done, cross_validation 4문장 생성.
- observer 재측정: **FinVision 73.67 → 80.67 (+7.0). Cross Check 0.0 → 10.0(만점).**
  - observer 채점식: `cap(len(cross_source_consistency)/3)*10` → 문장 3개 이상이면 만점.

### 관찰 (정직)
- 점수는 만점이지만, 이번 실행에서 4문장이 모두 "[교차 근거 부족]"이었다.
  - 원인: cross_checker의 fuzzy 매칭이 전체 문장 유사도 0.65로 엄격 → 서로 다른 문구의 출처들이 같은 수치($135M)를 말해도 '일치'로 안 잡힘.
  - observer는 문장 '개수'만 세므로 만점이 나오지만, 실제 교차검증 콘텐츠 품질은 아직 약함.
- 즉 '노출'은 됐고 점수는 올랐으나, '강화'(핵심수치 기반 agreement 탐지)는 추가 여지가 있음.

### 남은 다음 단계
1. cross_checker 강화: 핵심 수치/엔티티 기반 agreement 탐지(다른 문구라도 같은 $135M이면 '일치')로 실제 교차검증 품질 향상.
2. 남은 observer 약점: Official Sources(10 vs 18), Evidence(9 vs 12) — 실행 편차 포함, 표준 입력 생성기 필요.
3. 프론트에 cross_validation 렌더(unverified_gaps처럼).
---

## 2026-07-06 - (Claude Code) GPT·Gemini 딥리서치 전 출처 감사 (INDI/Wuxi 벤치마크)

### 목적
- 동일 질의(indie의 Wuxi 지분매각)에 대해 ChatGPT·Gemini Deep Research 결과를 **1차 원문과 전수 대조**.
- "어떤 출처를 가져오고 무엇을 버리는가"(검색행동) 파악 → FinVision 이식항목 도출.
- 원칙: 실제로 연 것만 '확인', 못 연 것(404/403봇/000/image-PDF)은 정직히 표기.

### 방법
- curl(+SEC User-Agent)·pdftotext(-enc UTF-8)·WebSearch·WebFetch로 URL 직접 fetch, HTTP 상태코드까지 기록.
- GPT 스캔 surface ~129개(인용 14) / Gemini 고유 URL 76개 전수 시도.

### 핵심 발견 1 — GPT의 교차검증이 '오표기 숫자' 위에 있었음 (정정)
- GPT는 英迪芯微 100% 거래가를 **4,926,588,081.49元(≈49.3亿)**로 보고 "ADK 34.3769%=960,834,355 pro-rata(2,795M) ↔ 4,926M 심각한 상충"이라 결론. GPT 스스로 "cninfo 렌더링 오류로 미열람"이라 flag한 미검증값.
- cninfo가 image-PDF(pdftotext 0자)로 막은 문서를 **pdf.dfcfw.com / file.finance.qq.com 텍스트 미러**로 추출(报告书草案 667K자, 独立财务顾问报告 538K자):
  - 英迪芯微 100% 지분 **交易作价 28.56亿元**(평가값 28.00亿, 市场法, 溢价率 2.00%).
  - **首期总对价 27.95亿元** = ADK현금 + 관리층주식 + 투자자 주식/현금 합.
  - ADK 34.3769% × 27.95亿 = **9.608亿 = 960,834,355元** → **소수점까지 정합**.
  - GPT의 49.3亿 = 交易价格(28.56亿) + 募集配套资金 ≈ 딜+조달 총규모를 '지분가격'으로 오표기한 것.
- 결론: **GPT의 정성판단(차등가격 존재)은 옳으나, 정량 상충(960M↔4,926M)은 허상. 실제는 ~2% 정합.**
- 차등가격 메커니즘(独立财务顾问报告 원문): 창始股东(ADK·Vincent Isen Wang)은 整体估值 기준 **현금 청산**; 투자자주주는 라운드별(B이전=할인, B이후=원가+연이자). → ADK가 왜 단독·현금·34.3769%로 SEC 공시됐는지 설명됨.

### 핵심 발견 2 — Gemini에서만 나온 신규 데이터
- **세율 ~10%**: investing.com Q3'25 콜 "$135M, net of applicable local taxes of roughly 10%" → 순액 ≈$121.5M(gross/net 논쟁의 정량 정합점).
- **indie 자기 표현이 분기마다 변함**: Q3'25/SEC 8-K = gross $135M(세전), Q1'26 콜(fool) = "$135M net cash proceeds". Gemini는 Q1"net", GPT는 SEC"gross" 채택 — 둘 다 indie 자기 말.
- Wuxi Q1'26 실매출 $21.4M, Gen8 레이더 $25M 주문(fool Q1 transcript).
- **OSRAM CMOS센서 €40M 인수**(indie 별개 딜): Gemini 포함(yolegroup+SEC EX99.1-216934), GPT는 "무관"이라 명시적 배제.

### 핵심 발견 3 — 지급구조·타깃 재무(중국 2차)
- 지급구조(eastmoney): 현금 11.63亿(40.7%) + 주식 16.93亿 = 28.56亿. ADK 현금 9.61亿은 그 현금분의 일부.
- 英迪芯微 개별재무(chnfund, 미감사): 2023 매출 4.94亿/2024 5.84亿; 순이익(주식보상 제외) 6287→4641만; 毛利>40%; 누적출하 3.5亿颗; 2017설립; 大众/현대기아/福特/GM 등 수출.

### 검색행동 관찰 (GPT vs Gemini)
- GPT: SEC+cninfo 1차만 load-bearing, aggregator/transcript 배제(규율 최강). 129→14 필터링 = **식별자충돌 노이즈 정확 배제**(301112=색상/유전자/벽지/세포, 1841925=토너/USDOT/pubmed, indie=음악, 英/信=한자사전). 단 세율·타깃재무 놓침, 4,926 오표기.
- Gemini: 넓은 그물(IR investors.indie.inc·q4cdn·nasdaq/businesswire 미러·SZSE disc.static·Sina공고 미러·transcript·중국뉴스·aggregator). 세율·Q1"net"·OSRAM 포착. 소싱 다양성 우위, 일부 2차 혼입.
- 둘 다 놓친 것: 28.56亿/27.95亿 차등가격 정합(=cninfo image-PDF 미열람) → FinVision이 텍스트 미러로 유일 확정.

### FinVision 이식항목 (이번 감사가 지목)
1. **accessible_resolver/alternate_finder**: cninfo가 image-PDF로 막으면 pdf.dfcfw.com·file.finance.qq.com 등 **텍스트 추출 가능 미러**를 탐색(동일 공시 다른 호스트). = GPT를 이긴 지점.
2. **cross_checker 규칙**: "열지 못한(미검증) 수치는 상충 판정 근거로 쓰지 않는다"(GPT 오류 재발 방지). + 교차관할 pro-rata 정합성 검증은 **정확한 분모(首期总对价 등)**로만.
3. **엔티티 해상도 US↔CN**: United Faith = 广州信邦智能装备 301112(매수인), ADK/Wuxi/英迪芯微 매핑.
4. **중국 PDF 추출**: 텍스트 PDF=pdftotext; image/CID PDF=OCR 폴백 or 텍스트 자매공시 우선 탐색.
5. **grounding 필터**: 식별자충돌(코드/CIK/한자/장르) 배제 = lead_follower._is_grounded_lead 목표 동작(GPT가 시연).

### 커버리지 (정직)
- GPT: 인용 1차 14개 전부 검증(12 직접 + ars/cninfo12-11은 미러·10-K로). 노이즈/2차 대표 다수 직접 fetch. 못 연 것=404(slug)/403봇월(marketscreener·simplywall·investing·businesswire·gurufocus·stocktitan·fintel)/000(eet-china·edgar-online)/image-PDF/JS(futunn) — 각각 대체 경로로 내용 확보. Cytek Wuxi=타사(오탐).
- Gemini: 76 고유 URL 전수 시도, ~42 판독. 차단분(403 Cloudflare·202·000·JS)은 IR/SEC/제목으로 대체 확보.
- SEC 옛 파일(FY23/24 10-K, Q1'25/Q2'24/Q2'22 10-Q, S-3, 424B3)=전부 딜 이전(Wuxi 의결권 64→54.7→55→59% 변천), 딜 내용 없음 확인.

### 비고
- 코드 변경 없음(감사·벤치마크 기록). 위 이식항목은 사용자 승인 후 착수.

---

## 2026-07-06 - (Claude Code) Critic에 결정론적 수치 정합 검사 이식 (시간축+산술)

### 배경 / 사고 이식
- GPT·Gemini 감사에서 배운 사고를 FinVision 심층(Critic)에 대입.
- 핵심 교훈: **산술을 LLM에 시키면 안 된다**(GPT가 못 연 숫자로 pro-rata 상충을 단정한 실수). 산술은 결정론적 코드가 담당.

### 추가 파일
- `backend/app/deep_research/agents/numeric_consistency.py` (신규, 순수/무네트워크):
  - 금액($M, RMB, 亿/万元)·퍼센트 추출 + gross/net 수식어·기준일·지분/세금 문맥 태깅.
  - **산술 정합**: pro-rata(부분≈전체×비율), 세율(net≈gross×(1-세율)) — 동일 통화끼리만.
  - **시간축/프레이밍**: 같은 금액이 gross·net으로 여러 출처에 갈려 등장하면 상충 플래그.
  - 원칙: 실제 추출된 콘텐츠만 대상(=열지 못한 값은 상충 근거 안 됨), 사실 생성 없음, 재확인 쿼리만 제안.

### 수정 파일
- `backend/app/deep_research/agents/critic.py`:
  - `numeric_consistency` import + `_augment_with_numeric(result, contents, iteration)` 헬퍼.
  - 성공경로(grounding 뒤)·폴백경로 둘 다에서 호출 → 상충을 gaps에, 재확인을 additional_queries에 병합.
  - 미해결 수치 상충이 있으면 iteration≤2에서 is_sufficient=false(재검색), 무한루프 방지. LLM 실패해도 결정론 검사는 유효.

### 검증 (오늘 실제 감사 숫자로)
- `backend/tests/test_numeric_consistency.py` 신규, unittest 7개 전부 통과:
  - pro-rata 정합: 960,834,355 ≈ 27.95亿 × 34.3769% ✓
  - 프레이밍 상충: 동일 $135M이 gross·net으로 3출처 ✓
  - 세율 재확인: gross $135M×(1-10%)=121.5M ≠ net $135M ✓
  - 정합 데이터 오탐 0 / 빈입력 / 중복 dedup ✓
  - Critic 폴백 경로에서 gaps·쿼리 병합 ✓
- py_compile OK(양 파일). LLM/네트워크 불필요.

### 효과
- Critic이 이제 "다출처 일치"를 넘어 **산술 정합 + 프레이밍(gross↔net) 변화**를 결정론적으로 잡는다.
- GPT가 당한 '미검증 숫자 상충 단정'을 구조적으로 차단(추출된 값만 계산).

### 다음 단계 (미착수)
1. numeric_consistency의 consistent/conflict를 최종 응답 `cross_validation`에도 노출(현재는 Critic 재검색 트리거로만).
2. 통화 교차(RMB↔USD 환율) 정합, 기간별 동일지표 변화(예: 12%/3% Jun→Dec) 검사 확장.
3. 프론트에 수치정합 배지 렌더.

### 추가(같은 날) - 수치정합을 cross_validation에 노출 완료
- `backend/app/deep_research/agents/synthesizer.py`:
  - import numeric_consistency + `_numeric_cross_validation(contents)` 헬퍼 추가.
  - synthesize()의 방어선4(cross_checker) 직후 방어선4b로 병합:
    `cross_validation = dedup(cross_validation + numeric_cross_validation(contents))`.
  - 정합(pro-rata·세율)·상충(gross↔net·세율) 문장이 최종 응답 cross_validation에 실림. 최대 8개.
  - LLM 산술 미사용, 실제 추출 contents만 대상(무할루시네이션 유지).
- 테스트: `test_numeric_consistency.py`에 TestSynthesizerExposure 추가 → unittest 8개 전부 통과.
- 결과: Critic(재검색 트리거) + Synthesizer(최종 노출) 양쪽에 수치정합 탑재 완료.

### 추가(같은 날) - 통화 교차(환율) 정합 확장
- `backend/app/deep_research/agents/numeric_consistency.py`:
  - `NumericMention.pos`(원문 오프셋) 추가 → '같은 문장 통화 등가액 쌍' 판별.
  - `find_cross_currency_inconsistencies()`: 근접(≤130자) 이종통화 금액쌍의 함축환율 계산.
    - RMB/USD 밴드 [5.0, 8.5](넓게 — 단위 亿/万 혼동·통화 오표기 같은 큰 오류만).
    - 밴드 내 → [환율 정합] 함축 FX 표기 / 밴드 밖 → [환율 이상] + 재확인 쿼리.
    - 출처간 함축환율 중앙값 대비 5% 초과 이탈 → [환율 상충].
  - analyze()에 병합 → Critic·Synthesizer 양쪽으로 자동 전파.
- 검증: 오늘 데이터 RMB 960,834,355 ≈ $135M → 함축 FX ≈ 7.12 정합. 10배 오표기 → 71.17 밴드밖 탐지. 단일통화 오탐 0.
- 테스트: TestCrossCurrency 3개 추가 → unittest 11개 전부 통과.
- 결과: cross_validation이 이제 pro-rata·프레이밍(gross↔net)·세율·환율 4종 결정론 검사를 노출.

### 추가(같은 날) - 프론트 수치 교차검증 배지 렌더
- `frontend/src/components/shared/StockResearchChat.jsx`:
  - `CrossValidationSection({ items })` 신규 컴포넌트 추가(UnverifiedGapsSection 스타일 준수, 접이식).
  - `result.cross_validation`을 3색 배지로 분류 렌더: 정합(초록 ✓)/주의(호박 !)/약함(회색 ·).
    - classify: /정합|출처 일치/→ok, /상충|재확인|이상/→warn, else→weak. [태그] 부분을 pill로 표시.
  - 헤더에 정합N·주의N 카운트 배지. 미검증항목 섹션 바로 위에 배치.
- 검증:
  - esbuild JSX 구문 OK.
  - classify/parse 로직을 백엔드 실제 출력 9종에 적용 → 9/9 분류 정확(node).
  - 브라우저 실렌더는 실제 리서치 결과(백엔드+Gemini API+쿼리 실행) 필요 → 미실행(정직 표기). 데이터 없으면 null 반환이라 무해.

### 추가(같은 날) - 실제 E2E 실행으로 수치검사 오탐 3종 발견·수정 + 배지 시각확인
백엔드(uvicorn)+실제 리서치 6회 실행. **실행이 유닛테스트로 안 잡히던 오탐을 드러냄**:
1. pro-rata 전역대조 폭발(8개 오탐): 코퍼스에 숫자 많으면 같은 비율로 우연히 맞는 무관한 쌍 다수.
   → 부분·전체·비율 **같은 출처 근접(≤240자)** + 전체 **100%/총액 문맥** 필수 + 금액하한(≥100만) + 오차 1.5%.
2. FX 오탐(4개): 작은 RMB 숫자가 큰 USD와 근접 → 함축FX≈0. → **양쪽 ≥100만** + 타당범위(밴드×10) 밖이면 침묵.
3. 세율 폭발(7개): 1.2% 잡음 세율로 거의같은 소액 gross≈net 매칭. → **타당세율 5~35%** + 금액하한 + 오차 1.2% + 상충은 'gross와 같은 금액 net'일 때만.
- 지분비율 토큰에서 일반 '占'(占营收) 제거, 지분 전용(股权/股本/持有/equity/stake…)만.
- 회귀 테스트 5개 추가 → unittest 16개 전부 통과.
- 최종 실행(job e162ef21): cross_validation 8개 = 환율 정합(7.12)·세율 정합·pro-rata + 교차근거부족 4. 잡음 폭발 제거됨.
- **프론트 배지 시각확인**: preview가 MiroFish 루트 밖 FinVision 프론트를 못 띄워, 실제 CrossValidationSection 코드+실제 백엔드 데이터를 정적 하네스로 렌더 → 스크린샷 확인(정합 초록 3, 교차근거부족 회색 4).
- 남은 경미 이슈(정직): 세율 정합 net$135M↔gross$150M(=전환사채, 문맥상 우연), pro-rata 10%(참이나 딜무관). 다음: 세율 정합에도 근접(co-location) 요건 추가로 정밀화.

### 추가(같은 날) - 세율 정합 co-location 정밀화
- `numeric_consistency.py` 세율검사: **'정합(consistent)'은 gross·net이 같은 출처 근접(≤240자)** 일 때만 성립(같은 거래로 함께 서술). '상충(conflict)'은 교차출처 유지(감사 핵심: gross=SEC, net=fool 흩어진 혼용 탐지).
  - matched net에 `n is not g` + 같은 출처·pos 근접 요건 추가. same_mag(상충)에도 `n is not g` 추가(단일출처 자기참조 오탐 제거).
- 효과: 실행에서 나온 우연 정합 'net $135M ≈ gross $150M(전환사채, 흩어짐)' 2줄 제거. 진짜 정합(같은 문장 gross$135M+net$121.5M+10%)·진짜 상충(교차출처 $135M gross/net)은 유지.
- 검증: FP 침묵 / TP정합 유지 / TP상충 유지 3케이스 + 회귀 테스트 1개 추가 → unittest 17개 전부 통과.
- 배지 재렌더: 정합 3→2, 잡음 제거된 깨끗한 화면 스크린샷 확인.
- 비고: 이 스크린샷은 정밀화 후 예상 출력(세율 2줄 제거 반영). 유닛테스트가 실제 실행 FP 시나리오를 재현해 검증. 전체 파이프라인 재실행은 미수행.

### 추가(같은 날) - 라이브 재확인 + 프론트 분류 버그 수정
- 백엔드 재기동(세율 co-location 코드) + 실제 리서치(job c2e1893a) 라이브 실행. cross_validation 12개:
  - **세율 정합 우연 오탐 0**(co-location 효과 확인). 대신 **프레이밍 상충 자동 탐지**: '$135M이 gross·net으로 10곳 등장'(RMB 960,834,355는 6곳) — GPT/Gemini 감사의 핵심 gross/net 텐션을 파이프라인이 자동 포착.
  - 세율 환산 재확인(9.98%·16.6%)=안전한 '재확인' 방향, 환율 정합 7.12 정확.
- **프론트 분류 버그 발견·수정**: `classify(s)`가 문자열 전체를 봐서 본문의 "35% 이상"의 '이상'을 warn(주의)으로 오분류. → **태그만 보고 분류**하도록 수정(StockResearchChat.jsx + 데모). esbuild OK, 태그기반 분류 7/7.
- 검증: 스냅샷 '정합 2·주의 4'(오분류 교정 전이면 주의 5), 문제항목이 weak('·')로 정상. 스크린샷 도구는 CDN 무거운 데모에서 타임아웃(스냅샷·로직테스트로 대체 확인).

### 추가(같은 날) - 크로스출처 pro-rata 엔티티 연결 복원
- 배경: co-location 강화로 안전해졌지만, 감사의 핵심(SEC 지분가 960,834,355 ↔ 中 100%가 27.95亿, 흩어진 출처) 크로스출처 정합을 잃었음. 이를 안전하게 복원.
- `numeric_consistency.py`:
  - `NumericMention.anchors`(주변 고유 식별자: 6자리코드/티커/라틴 고유명, 통화·법인·규제 약어는 stoplist 제외) 추가 + `_extract_anchors()`.
  - `find_cross_source_prorata()` 신규: '지분블록(part+지분% 근접)' × '전체(100%/총액 문맥)'를 환산.
    - 오탐 방지: **무모호(전체후보 정확히 1개)** + **금액하한 ≥1천만** + 매칭오차 ≤1% + **(공유 앵커) 또는 (거의 정확 ≤0.3%)** 일 때만 인정.
    - 코어퍼런스(标的公司=英迪芯微, 앵커 공유 X)는 '거의 정확' 경로로 커버.
  - analyze()에 병합, 결과 태그 `[pro-rata 정합·교차출처]`(원문 확인 권장 문구 포함).
- 검증: 감사 케이스(정확일치)·앵커공유(301112)·무관침묵·모호(2+전체)침묵 4케이스 + 기존 2개 갱신 → unittest **21개 전부 통과**.
- 프론트: 새 태그가 태그기반 classify로 초록(정합) 분류됨(수정 불필요, esbuild OK).
- 남은 한계: 앵커는 코드/티커/라틴만(CJK 고유명 미추출) → CJK는 정확일치 경로 의존. 향후 CJK 엔티티 추출 추가 여지.

### 추가(같은 날) - 크로스출처 pro-rata 라이브 검증 + 라운드비율 오탐 수정
- 라이브 실행(job c2f5b442)에서 크로스출처 오탐 2개 관측: `4,001만 ≈ 4亿 × 10%`, `40,000만 ≈ 40亿 × 10%`.
  - 원인: 라운드 비율(10%)은 라운드 금액과 우연히 '정확히' 맞아 exact-path(≤0.3%) 통과.
  - 수정: 앵커 없는 exact-path는 **정밀(소수부 있는) 비율만 인정**(34.3769%는 통과, 10%·40% 정수 배제). 공유 앵커가 있으면 라운드도 허용.
  - 회귀 테스트 2개 추가 → unittest **23개 전부 통과**. 라운드 오탐 침묵 / 정밀 감사케이스 유지 확인.
- 재실행(job dd56fbb4) 성공: `[pro-rata 정합·교차출처]` 오탐 0. 환율 정합 7.12, 프레이밍이 딜 핵심 포착.
- 부수 관측(오늘 작업과 별개, 기존 검사 노이즈): ①프레이밍이 소액($5M/$10M/$600k)까지 과다 발화 ②co-located pro-rata가 근-100% 비율(99.26%)로 우연 일치. → 후속 정밀화 후보.
- 별개 기존 버그 관측: 합성 시 LLM이 confidence="none" 반환 → ConfidenceLevel enum crash → 폴백(결과 0). numeric 변경과 무관, synthesizer 견고성 이슈.

### 추가(같은 날) - synthesizer confidence 방어 (리서치 전체 폴백 버그 수정)
- 버그: LLM이 key_finding의 confidence를 'none' 등 유효하지 않은 값으로 반환 → `ConfidenceLevel(f.get("confidence","medium"))`가 ValueError → 합성 전체 crash → 폴백(key_findings·cross_validation 0). 실제 라이브 7회 중 2회 이 버그로 결과가 통째로 날아감.
- 수정 `backend/app/deep_research/agents/synthesizer.py`:
  - `_coerce_confidence(value)` 헬퍼 추가 — high/medium/low(+한글·약어) 매핑, 그 외/None/오탈자는 MEDIUM. 예외 안 던짐.
  - key_findings 생성을 **finding별 try/except 루프**로 변경 — 한 항목이 깨져도 그 항목만 스킵, 리서치 전체는 살림.
- 검증: `backend/tests/test_synthesizer_confidence.py` 신규 unittest 4개 통과('none'/null/한글/약어/KeyFinding no-crash). numeric 23개 + 앱 import(43 routes) 회귀 OK.
- 비고: 'none' 반환은 LLM 출력 변동성이라 라이브 재현이 불확실 → 정확한 crash 경로를 단위테스트로 직접 검증.

### 추가(같은 날) - 프레이밍 과다발화·근100% pro-rata 노이즈 억제
- #2 프레이밍(gross↔net) 과다발화: `numeric_consistency.py` `find_framing_conflicts`에 **금액 하한(≥100만) + 출처수 하한(≥3)** 추가. 소액 line item($600k/$5M)이 gross/net 단어와 우연 근접해 과다 발화하던 노이즈 억제. 딜 핵심 금액은 실측상 6~10 출처라 ≥3 안전.
- #3 co-located/교차출처 pro-rata: 지분비율 허용범위를 **[1%, 95%]** 로 제한(`_PRORATA_RATIO_MIN/MAX`). 근-100%(99.26%)가 두 '거의 같은' 큰 숫자를 우연히 맞추는 노이즈 배제.
- 검증: 회귀 테스트 3개 추가(소액·소수출처 억제 / 딜금액 유지 / 근100% 배제) → numeric **26개** 전부 통과. confidence 4개도 OK.
- 이로써 라이브에서 관측된 4대 노이즈(pro-rata 전역/라운드, FX 소액, 세율 잡음, 프레이밍 과다, 근100%) + confidence crash까지 모두 대응.

### 추가(2026-07-13) - Fable 5 리뷰 확정 버그 5종 수정 (전부 실측 재현 후)
모든 버그를 실제 코드로 재현 확인한 뒤 수정. (버그4는 첫 재현 때 버그1이 값을 망가뜨려 우연히 마스킹됨 → 깨끗한 재현으로 확정.)
- **버그1(치명)**: `亿美元`→RMB, `人民币9.6亿元`→9.6(1e8배 축소) 통화/단위 오분류. `numeric_consistency.py` `_MONEY_PATTERNS` 전면 재작성 — CJK 숫자+단위(亿/万)+통화접미 캡처, 통화 매핑(美元→USD/港元→HKD/元→RMB), 접두 없는 `X元`도 처리, `_parse_money()` 도입. `_scale` 제거.
- **버그2(치명)**: `HK$`→USD 오분류(7.8배 오류), 기호 없는 `USD 135 million` 미추출. 지역$ 접두(HK/US/NT/S/A/C) + 통화어 접두 패턴 추가.
- **버그3**: `lstrip("www.")`가 문자집합 제거라 `wsj.com`→`sj.com`. 8개 파일 12곳 전부 `removeprefix("www.")`로 치환(출처 신뢰도 가중이 w시작 도메인에서 깨져 있던 것).
- **버그4(역설)**: 크로스출처 pro-rata의 `cands`가 mention 단위라, 전체금액이 여러 출처에 인용될수록(=더 검증될수록) 침묵. `(round(value))`로 dedup — 서로 다른 값이 2+일 때만 모호 처리.
- **버그5**: `cross_checker._find_contradicting_numbers`가 `\d+\.?\d*`로 `960,834,355`→['960','834','355'] 파편화 + 부분문자열 비교. `_numeric_values()`(콤마 보존) + 값 기반 비교로 재작성.
- 검증: 각 버그 실측 재현→수정→재현 확인. 회귀 테스트 `TestFableReviewedBugs` 7개 추가 → numeric **33개** 전부 통과. 앱 부팅 OK.
- 설계 관찰(버그 아님, 후속): 앵커 게이트 약함(영어 첫단어), 프레이밍 신디케이트 복제 취약(근사중복 dedup 필요), 프론트 classify ok/warn 순서. Fable §5 미래자문(미러탐색·가젯티어·PP-OCR·structured output)은 별도 로드맵.

### 추가(2026-07-13) - Fable 5 설계 관찰 5종 반영 (전부 실측 재현 후)
버그 5종에 이어 '설계 관찰'도 각각 실측 재현→수정.
1. **앵커 게이트 약함**: `[A-Z][a-z]{3,}`가 'Company/Shares/Pursuant' 등 boilerplate를 앵커로 잡아, 무관 문서가 'Company' 공유 + 라운드비율로 거짓 교차출처 정합. → `_ANCHOR_STOP`에 흔한 대문자 boilerplate ~70개 추가. 진짜 고유명(United/Wuxi/301112)·티커는 유지. (근본해결=가젯티어는 §5-3 로드맵.)
2. **pro-rata 정합 전용 명시 + 실제 오차 표기**: 설계의도 주석 추가(서로 다른 값 비율불일치를 '모순'으로 단정하면 '미검증값 상충금지' 위반 → 정합만). 메시지 '오차 ≤1.5%'(상한) → '오차 0.00%'(실제값), 교차출처도 동일.
3. **프레이밍 신디케이트 복제 취약**: 동일 기사 3전재 → '출처 3곳'으로 ≥3 통과. `analyze()`에 `_dedup_contents()`(정규화 서명 ≥60자 동일=verbatim 중복) 추가 → 신디케이트 1출처로 계수, 서로 다른 기사는 유지. (cross_checker 'N개 출처 일치'도 같은 취약 → 파이프라인 레벨 dedup은 후속.)
4. **critic confidence 크래시**: LLM이 confidence를 'high' 등 문자열로 주면 `confidence<0.85` 비교/`GapAnalysis(float)` 검증에서 예외 → critic 평가 전체 폴백. `_coerce_float_confidence()` 추가(문자열/비정상/클램프 처리).
5. **프론트 classify 순서**: ok가 warn보다 먼저 → '정합·상충' 공존 시 ok 오분류. warn 우선으로 재배열(StockResearchChat.jsx + 데모). esbuild OK, 로직 6/6.
- 검증: 회귀 테스트 `TestFableDesignObservations`(4) + critic confidence(1) 추가 → numeric **37개** + confidence **5개** 전부 통과. 앱 부팅 OK.
- Fable §5(미러탐색 akshare/Brave/Exa/CDX, 가젯티어 GLEIF/OpenFIGI, PP-OCRv6, structured output SDK통일)은 버그 아닌 미래 아키텍처 자문 → 로드맵으로 보존(즉시 수정 대상 아님).

### 추가(2026-07-13) - Fable 5 라운드3~5 검토 반영 1차 (전 항목 실측 재현 후 수정)
우선순위(Fable 권고: 통화파서→싱글턴→lstrip→(AI)→sentiment None→합성reaction→estimate값비교→파라미터·Tavily→…) 순서로 진행. 각 항목 실측 재현 → 수정 → 검증.
1. **[S1] 싱글턴 상태 누적**: router 전역 `_pipeline`에서 `Searcher._url_seen/_total_queries`·`Extractor._extracted_urls`·토큰카운터가 잡 간 누적(두 번째 리서치부터 출처 영구 스킵, 쿼리상한이 프로세스 총량화, 비용 합산 오염). → `Searcher.reset()`/`Extractor.reset()`/`reset_usage()`(planner·critic·synthesizer) 추가, pipeline.run 시작부 배선. + `all_contents` 조립 후 URL 명시 dedup(프레이밍 출처 부풀림 방지 안전벨트).
2. **[S1] (AI)/(IT) 티커 오탐**(ingest2/classify/tickers.py): `(대문자≤5)` 괄호가 실존 티커 AI(C3.ai)/IT(Gartner)와 충돌. → `_COMMON_WORD_TICKERS` 스톱리스트(1글자 전부+상용어 약어): 괄호형은 차단, `$AI`는 인정. + `_ALIAS_RE`에 `(?!-)`(Meta-analysis→META 차단). Fable 재현 케이스 6/6, classify 테스트 11개 통과(회귀 2개 추가).
3. **sentiment None 크래시**(api/earnings.py 150·197): `.get("sentiment_score",50)`은 값이 None이면 None → `>50` 비교 TypeError로 티커 500. → None 명시 방어 2곳.
4. **[무할루시네이션] 합성 avg_reaction 제거**(api/earnings.py 캐시경로): `(sentiment-50)*0.1` 지어낸 %가 실측처럼 프론트 노출. → `avg_reaction: None` + `avg_sentiment`(실데이터) + `estimated: True`로 정직화. 프론트(EarningsSimulator)는 실측 있으면 %, 없으면 "감성 XX" 표기(툴팁 명시). esbuild OK.
5. **estimate 값비교**(earnings_analyzer): `num_est_sources>=2`만 보고 값 비교 없음(1.20 vs 0.85도 '검증됨'). → `_estimates_agree()`(상대오차≤2%) 도입, 소스 다수+값 상이면 미검증. 직접 검증(불일치→미검증/일치→검증) 통과.
6. **플래너 (X) 라벨 오타**: 올바른 예에 (X) → (O).
7. **insider/divestiture 부분문자열**: `"rsu" in "pursue"` 오탐 → ASCII는 \b 단어경계, CJK는 부분매치 유지(`_kw_match`). 'pursuing' False/'RSU' True 검증.
8. **Tavily 키 영구은퇴 → 15분 쿨다운**: `_exhausted_keys`(set) → `_exhausted_at`(시각 dict), 쿨다운 경과 시 자동 복귀. chat_service가 쓰는 공개 함수 시그니처 유지. 쿨다운 전/후 동작 검증.
9. **검색 파라미터명 불일치**: parallel `num_results`→`max_results` 표준화(+하위호환 alias), tavily에도 alias, official_source_searcher 호출 2곳 통일 — 결과수 제한이 **kwargs로 조용히 무시되던 것 해소.
10. **비용가드 실동작화**: 반사 루프에 `MAX_COST_USD_PER_RUN` 검사 추가(기존: 정의만 되고 장식).
11. **datetime.utcnow() 제거**: deep_research 7곳 → `datetime.now(timezone.utc)` + import 정비.
- 검증: numeric 37 + confidence 5 + classify 11 전부 통과, 앱 부팅(43 routes) OK.
- **미착수(다음 순서)**: 페이월 복구 메인경로 배선(S1), 신뢰도 5중화 단일화(S2), fool.com 요청 캡, sec_edgar CIK 정확매칭·rsu, jurisdiction_detector 대문자 오탐, _jobs TTL, 진행률 역행, pro-rata 역방향, 지표 정의 3중 불일치, comparator 재설계, SEC XBRL 5번째 검사, 8워커 배치화, deadline 토큰, 신뢰도 레지스트리. (버그 아닌 아키텍처 자문: structured output 이식, common/ 패키지, 미러탐색은 로드맵.)

### 추가(2026-07-13) - [S1] 페이월 복구 메인경로 배선 (Fable 라운드3)
- 문제: extractor.BLOCKED_DOMAINS가 wsj/ft/bloomberg를 추출 시도조차 없이 버리는데, 그 도메인들을 Wayback으로 복구하는 `_extract_with_recovery`는 Discovery(3d) 경로에서만 호출 → 메인 검색의 페이월 기사는 복구 기회 없이 소멸. "접근가능 미러 찾기가 승부처"라는 감사 결론이 메인 경로에 미배선.
- 수정 `pipeline.py`:
  1. `_extract_with_recovery`에 `max_recovery=5` 상한 추가(메인 경로는 결과가 많아 Wayback 순차조회 지연 방지) + 게이트 URL dedup.
  2. 메인 초기 추출(구 174행)과 반사루프 추가검색(구 353행, max_recovery=3)을 recovery 경로로 교체.
  3. `metadata.recovered_sources`를 `=`(마지막 할당이 덮어씀) → `+=` 누적으로 — 메인/반사/discovery 복구분 합산.
- 부수 확인: lstrip→removeprefix 수정으로 `www.wsj.com`→`wsj.com` BLOCKED 매치 정상(유령상태 해소). DISCOVERY_ENABLED 플래그는 lead-follower만 게이트 — 메인 복구는 플래그 무관 동작. alternate_finder 배선은 run()에서 무조건 수행됨.
- 검증(오프라인 목): 게이트 wsj URL → wayback 스냅샷 URL로 복구·추출 recovered=1 ✓, 게이트 10개 시 Wayback 조회 5회 상한 준수 ✓. numeric 37+confidence 5 회귀, 앱 부팅(43 routes) OK.

### 추가(2026-07-13) - fool.com 트랜스크립트 폴백 요청 캡 (Fable 라운드5 #22)
- 실측: 3단계 폴백이 무제한 — slugs(~4)×quarters(캡 없음 2~4)×날짜(±3, 7일)×patterns(2) = 최악 **~448회** HTTP 요청/분기(Fable 추정 140~280보다 큼). IP 차단 위험 실재. 사이트맵은 월별 캐시라 안전 — 폭탄은 순수 3단계.
- 수정 `gemini_guidance.py`:
  - `_MAX_FOOL_FETCH_ATTEMPTS = 12` — 호출당 fool.com 본문 fetch 전 단계 합산 캡, 도달 시 경고 로그 후 중단.
  - 3단계 quarters를 `[:2]`로 캡(1·2단계와 일치).
  - docstring 정정: "3단계 DuckDuckGo 검색"(실제 코드와 불일치) → 사이트맵/슬러그 변형으로 사실화.
- 검증(목): 전부 실패 최악 케이스 시도 정확히 12회(캡 로그 확인), 1단계 성공 시 1회 조기 종료. guidance_accuracy 테스트 13개·앱 부팅 회귀 OK.

### 추가(2026-07-13) - sec_edgar 4종 수정 (Fable 라운드3 잔여 #4)
- ① **CIK 부분문자열 매칭**: `ticker.upper() in ent_name`은 F/GM 같은 짧은 티커가 거의 모든 회사명에 매치돼 엉뚱한 CIK. → EDGAR display_names의 `"(TICKER)"` 괄호 정확 매칭 + ticker_symbol 정확 일치만. (F vs FASTENAL 오탐 차단, 미매치 시 None 반환 = 틀린 CIK보다 안전.)
- ② **`"rsu" in "pursue"` 부분문자열**: pipeline과 동일 처방 — ASCII는 \b 단어경계, CJK는 부분매치 유지.
- ③ datetime.utcnow(): 이전 배치에서 이미 수정(잔존 0 확인).
- ④ **파생거래 수량 누락**: grants(A) 등은 수량이 transactionShares가 아니라 underlyingSecurityShares에만 있는 경우 존재 → 폴백 추가 (XML 파싱 검증: 이전 0 → 5000).
- 검증: 4케이스 매칭 시뮬레이션 전부 통과('pursuing' False/'RSU' True/'임원' True), sec_client 테스트 회귀, 앱 부팅 OK.

### 추가(2026-07-13) - jurisdiction_detector 대문자 오탐 수정 (Fable 라운드3 잔여 #5)
- 재현: "OPEC production cuts and NATO summit ... in China" → OPEC(+2)·NATO(+2)가 US 티커로 잡혀 **primary=US 오판**(US 4 vs CN 1).
- 수정 (Fable 처방 = SEC 실존 티커셋 positive 검증):
  - `_load_sec_tickers()`: SEC company_tickers.json lazy 로드(프로세스당 1회 시도) — ingest2 파일 캐시 재사용 우선(경로 3후보), 없으면 6s 타임아웃 다운로드, 실패 시 None 폴백.
  - `_detect_tickers()`: ①거래소/기관 약어 ②`_NON_TICKER_ACRONYMS`(OPEC/NATO/COVID/WHO/USD/EBIT 등 — 미로드 폴백 방어) ③`_AMBIGUOUS_TICKERS`(AI/IT/ALL 등 상용어 충돌 실존티커 — 관할 신호로는 노이즈) 제외 + ④SEC셋 로드 시 실존 티커만 인정.
- 검증: A)폴백 경로 — OPEC/NATO/COVID 제외·INDI 유지·**primary US→CN 교정**. B)실셋 — WUXI(비실존)/NATO/AI 제외. C)라이브 다운로드 10,408개(INDI∈, OPEC∉). numeric 37 회귀·앱 부팅 OK.
- 비고: backend가 ingest2를 import하지 않고 '파일 캐시'만 공유(코드베이스 결합 없이 데이터 재사용). backend 실행으로 캐시가 없으면 자체 다운로드.

### 추가(2026-07-13) - 소규모 3건 배치 (Fable 라운드3 S3 잔여)
1. **_jobs/_job_queues TTL**: 삭제 코드가 없어 완료 리포트 전문이 프로세스 메모리에 무한 축적. → `_update_job_status`가 DONE/FAILED 시각을 `_job_finished_at`에 기록, `create_job`에서 TTL(3600s) 경과 잡 lazy 정리. 검증: 61분 지난 완료 잡 정리·실행중/새 잡 보존.
2. **진행률 역행**: 50(추출)→32(공식)→52→55→45(discovery) 등 4지점 역행으로 프론트 진행바가 왔다갔다. → ①emit에 **단조 클램프**(`pct=max(pct,last)`) — 이후 누가 숫자를 잘못 넣어도 구조적으로 역행 불가, ②명백한 역행값 재배열(32→51, 36→53, 45→58, 48→59, 반사 50+i*5→60+i*4). 검증: 역행 주입 [35,50,32,52,45,58]→[35,50,50,52,52,58].
3. **pro-rata 대칭(역방향) 검증**: 정방향(part/ratio≈whole)만 보던 것에 역방향(part≈whole×ratio) 오차도 함께 요구 — 분모 기준이 달라 경계에서 어긋나는 '전체↔부분 착각' 케이스 차단(Fable 제안). co-located·크로스출처 양쪽 적용. 진짜 정합(오차≈0)은 무영향 — numeric 37개 전부 통과로 확인.
- 앱 부팅(43 routes) OK.

### 추가(2026-07-15) - 지표 정의 3중 불일치 통일 (Fable 라운드5 #23)
- 실측 확정: **재고회전율** 카드=COGS/평균재고, SEC차트·yf차트·분기=매출/기말재고, 툴팁="매출÷재고"(카드 값과 모순). **ROIC** 카드=NOPAT/(총부채+자기자본), 연간차트 2곳·분기=NOPAT/(총자산−유동부채). **asset/receivables** 카드=평균, 차트=기말.
- 통일 방침: 데이터 가용성 기준 — SEC 파생엔 total_debt 개념이 없어(XBRL 단일 개념 부재) ROIC는 **카드를 차트 정의(총자산−유동부채, operating approach)로**; 회전율은 **차트를 카드 공식(재고 분자=매출원가, 분모=평균 잔액)으로**.
- 수정:
  - `yfinance_client.py`: ①ROIC 카드 IC=총자산−유동부채(이미 로드된 변수 재사용) ②연간 _ratio 2벌(SEC파생·yf폴백)에 `avg_den`(전기말+당기말)/2 옵션 ③연간 회전율 3종 avg_den 적용+재고 분자 COGS ④분기 재고 분자 COGS(분모는 분기말 유지 — 연속 스냅샷이라 평균 의미 약함, 주석 명기) ⑤get_metric_history 요청 필드에 annual/quarterlyCostOfRevenue 추가.
  - `sec_client.py`: BLOCKS에 cost_of_revenue(CostOfRevenue/CostOfGoodsAndServicesSold/CostOfGoodsSold) 추가.
  - `StockDetail.jsx` 툴팁 4종 정직화: 재고회전율 "매출원가÷평균 재고", 자산 "매출÷평균 총자산", 채권 "매출÷평균 매출채권", ROIC "NOPAT÷투하자본(총자산−유동부채)".
- 검증: avg_den 산술(COGS100/avg(8,10)=11.11 등) 정확, ROIC 카드=차트 동일입력 동일값, esbuild OK, sec_client 20+stock_profile_ai 25 회귀, 앱 부팅 OK.
- 주의: 기존 캐시된 차트 값과 새 계산값이 다를 수 있음(정의가 바뀌었으므로 의도된 변화).

### 추가(2026-07-15) - 신뢰도 5중화 단일화 (Fable 라운드3 S2, 마지막 중규모)
- 실측 확정: wsj가 5곳에서 high(extractor)/7(cross_checker)/0.65 medium(evidence_ranker)/누락(raw_sources)/Tier2(synthesizer 프롬프트)로 상충. seekingalpha는 evidence_ranker만 LOW, 나머지 누락.
- 수정 — `source_registry.py`를 단일 진실 소스로:
  - `MEDIA_TIER2_DOMAINS`(Tier-1 미디어 13)·`MEDIA_TIER3_DOMAINS`(전문분석 5)·`LOW_TRUST_DOMAINS`(자동생성/루머/소셜 17) + `get_domain_tier/weight/credibility()` 헬퍼(서브도메인 endswith 매칭, gov/edu 폴백).
  - 파생 전환 5곳: ①evidence_ranker — 로컬 정규식 2벌 제거, tier→(점수,cred) 매핑(미디어 tier2는 cred HIGH 유지·점수만 0.75로 공식 0.85 아래), 비도메인 패턴(rumor/gossip)만 로컬 ②cross_checker — _DOMAIN_WEIGHT 26줄 제거 → get_domain_weight ③extractor — HIGH/LOW 집합 제거 → tier 판정 ④raw_sources.get_by_domain_priority → tier 파생 ⑤synthesizer 프롬프트 Tier 표를 레지스트리와 동기화(정적, 참조 주석).
- 검증: 일관성 매트릭스 — sec.gov(1/10/high/1.0/high)·wsj(2/7/high/0.75/high)·cnbc(3/5/medium)·seekingalpha(4/1/low)·미등록(2/medium) **5도메인×5소비처 전부 일치 PASS**. numeric 37+confidence 5 회귀, 앱 부팅 OK.
- 효과: 신뢰도 튜닝이 한 파일 수정으로 전 시스템 반영. seekingalpha/fool 등이 cross_checker 가중치에서도 1로 강등(이전 기본 2), wsj가 raw_sources 우선순위에 처음으로 편입.

### 추가(2026-07-15) - [로드맵→구현] SEC XBRL 원장 대조 (방어선 4c) — 5번째 검사
Fable이 "투자수익률 최고"라 한 항목. 껍데기 금지 조건으로 라이브까지 완주.
- **신규 `agents/xbrl_ledger.py`**: data.sec.gov companyfacts(무료·키불필요) → us-gaap USD 원장(≥$1M) → 값 정렬 bisect 근접 대조. CIK는 services.sec_client.get_cik(프로젝트 단일 소스) 재사용, 파일캐시(TTL 1일)+프로세스캐시, httpx async, 전 경로 실패 침묵.
- **원칙**: 확인 전용 — 원장 미존재 수치는 침묵(딜 대가·백로그 등 재무제표 밖 수치가 정상 존재 → '없음=오류' 단정은 미검증 상충 단정). 태그 `[원장 일치]`.
- **라이브가 잡아낸 것 3건 (전부 수정)**:
  1. 라운드 값($135M) 근접 오탐 — 0.5%에서 2023 영업손실(-135,423,000, 0.31%), 0.1%로 조여도 2022 Liabilities(135,070,000, 0.05%)와 겹침(5,148개 원장에서 구조적) → **라운드 $1M 단위 값은 '정수 정확 일치'만 인정**(예: $150M ↔ 사모발행 150,000,000은 정당 통과).
  2. DEF 14A의 fy/fp 누락 → "( , DEF 14A)" 빈 라벨 → end 날짜 폴백.
  3. **한국어 금액 표기 미지원으로 원장 대조 무력화** — 첫 E2E에서 리포트가 "약 1억 3,500만 달러"라 USD 추출 0. → numeric_consistency에 한국어 조/억/만 패턴 추가(ko_jo/ko_eok/ko_man, 통화어 달러/원/위안/엔/유로). '조' 미지원 시 '3조 5,000억'이 5,000억으로 잡히는 오값까지 잡음. 환율·프레이밍 검사에도 자동 반영.
- **배선**: synthesize(context=) 추가, pipeline이 request.context 전달, 방어선 4b 직후 4c로 summary+key_findings+sections 텍스트 대조 → cross_validation 병합. 프론트 classify `/정합|일치/`로 '원장 일치' 초록.
- **검증**:
  - 픽스처 unittest 11개(`tests/test_xbrl_ledger.py`) + 한국어 파싱 5개(numeric에 추가) — 총 numeric 42+xbrl 11 통과.
  - 라이브: INDI 원장 5,148건 구축, 감사 확정 수치 4건 정확 매치($174.4M↔174,433,000 / $10.3M↔10,281,000 / $150.7M↔-150,712,000 / $150M↔150,000,000 정확일치).
  - **E2E 라이브**: 실제 리서치(job 9e7337ef)에서 [원장 일치] 3건 — "5,370만 달러"≈Revenue 53,676,000(2025 Q3)·"3,830만 달러"≈NetIncomeLoss -38,289,000·"1,130만 달러"≈ContractAsset 11,302,000. LLM 한국어 리포트→파서→SEC 원장 전 구간 관통.
- 부수 관측: 이번 run들에서 key_findings 추출 변동(0~4개) — structured output 이식(로드맵)의 근거 재확인. 진행률 단조증가(15→35→51→58→70→82)도 라이브 확인.

### 추가(2026-07-15) - [로드맵→구현] 구조화 출력 이식 (google-genai response_schema) — deep_research 4개 에이전트
근거: 라이브 실측에서 2단계 추출 key_findings가 0~4개로 변동(자유텍스트 JSON 파싱 의존). ingest2/classify/deep.py에서 이미 운용 중인 패턴(resp.parsed → model_validate_json)을 본체에 이식.
- 신규 — `backend/app/deep_research/llm_client.py`: 구조화 출력 단일 진입점.
  - `generate_structured(prompt, schema, model, timeout_s, fallback_model, tag)` — response_mime_type=application/json + response_schema(Pydantic/list) 강제, quota(429)면 fallback_model 1회 재시도(기존 verify 폴백 관행 유지), 모든 실패는 None+경고(호출부가 레거시로 폴백 — 동작 후퇴 없음). 토큰은 usage_metadata 실측 우선.
- 배선 5곳 (전부 '구조화 1차 → 레거시 2차' 폴백 계약):
  - ①planner.plan → `PlanOut/SubQueryOut` ②critic.evaluate → `GapOut/AdditionalQueryOut`(confidence float 강제) ③synthesizer._extract_metadata → `MetadataOut`(confidence를 Literal["high","medium","low"]로 API 레벨 enum 강제 — 'none' 크래시 계열 원천 차단) ④synthesizer._self_verify → `VerifiedReportOut` + 빈 껍데기 방어(전 필드 기본값이면 원본 유지 — 검증 패스가 보고서를 지우는 사고 차단) ⑤lead_follower._extract_leads → `list[str]`.
  - synthesizer 1단계(마크다운 서술)와 chat_service(자유텍스트)는 의도적으로 제외 — JSON이 아님.
- 검증 — 단위 19(신규 test_structured_output.py: 폴백 계약·빈껍데기 방어·iteration-1 강제 규칙의 구조화 경로 적용·visited 필터) + 회귀(xbrl 11·numeric 42·confidence 5) 전부 PASS.
- 검증 — 라이브 2건: ①단독 호출 — PlanOut(중첩 스키마) 10개 sub_queries + MetadataOut Literal enum 강제 실동작. ②풀 파이프라인 E2E(INDI, job f7ceb552) — INFO 로그로 [planner] 구조화(10쿼리), [critic] 구조화 ×5 이터레이션, [synthesizer] 2단계 추출(구조화) findings 4/timeline 7, 자기 검증 패스 완료(구조화) 전부 확인. 파싱 실패 0건. 보고서 완주(sections 4, cv 10, xbrl 원장 5,148항목 구축·확인전용 침묵 정상).
- 관측: 자기 검증 패스가 findings 4→2, timeline 7→3으로 미검증 항목을 실제로 쳐냄([unverified] 태깅 포함) — 방어선 5가 구조화 경로에서도 유효.

### 추가(2026-07-15) - [로드맵→구현] comparator.py 재설계 — 수량 편향 제거 + Pairwise 상대비교
Fable 리뷰의 유일한 '재설계'급 항목. research_lab/langfuse_deep_research_observer/comparator.py 전면 재작성(서비스 코드 격리 유지, LLM 무관여).
- 문제(구버전): 전 항목이 len(...)/N 카운트 채점 → 물량공세가 무조건 승(제네릭 쿼리 20개 > 앵커된 3개, 장문 답변 > 짧고 정확한 답변). 또 엔진 자기신고 reliability_score를 엔진 간 비교 → 자기채점 인플레가 그대로 순위.
- 재설계 원칙 4가지:
  ①카운트→비율/품질: 전 지표 [0,1] 비율(앵커율·비중복률·공식비중·검색수율·티어가중). 수집량 늘려도 비율 나빠지면 감점. ②자기신고 배제: 근거 품질은 결정론적 도메인 티어(backend source_registry 동기화 미러)로만. ③N/A 재정규화: 로그 형식상 측정 불가 항목은 0점 아닌 제외+가용가중치 100점 재정규화(로그 형식 편향 제거). ④Pairwise: 절대 임계값 대신 같은 질의 엔진쌍 직접 대조(항목별 승패 ±10%, 종합 ±3점, 상대만 찾은 공식 도메인 병기).
- 카테고리별 신규 지표: jurisdiction=주장∩증거 자카드(과다주장 감점), query=앵커율/비중복/공식비중/다국어 평균, official=인용 공식비중+관할별 매칭, evidence=티어 가중평균, search=쿼리당 고유도메인 수율+비중복, cross=건수×다도메인 게이트(단일도메인 교차검증 반감), gap=미검증 명시, answer=인용·구조·한계(길이 무보상).
- 라이브 발견·수정: 리포트에 "official-source query ratio 122%"(불가능값) — 파서가 generated/official 리스트를 독립 구축해 official/generated가 1.0 초과. _official_query_ratio 분모를 두 집합 토큰시그니처 합집합으로 교정 → 60%/89% 정상화. query_generation component도 동일 헬퍼로 통일.
- 검증: test_comparator.py 17개 신규 PASS — 핵심 회귀(품질<소량>이 물량<대량>을 이김: total/query/evidence/answer 전부), 자기신고 무시, 장문무구조 0점, 과다관할 감점, 단일도메인 교차검증 반감, N/A 재정규화, 비율 1.0 상한, pairwise 승패·상대전용도메인, 개선원석(저신뢰의존/공식누락/갭). 샘플 3자(gemini/openai/finvision) run_compare 실동작 확인. 죽은코드 0·미사용import 0(ast 검사). README 비교항목표+채점원칙+Pairwise 섹션 갱신.

### 추가(2026-07-15) - [로드맵→구현] CI (GitHub Actions) — 전 테스트 회귀 방지 캡스톤
이번 세션에서 쌓은 전 테스트를 회귀 방지로 고정. "확실히 green" 요구에 따라 껍데기 없이 실측 기반으로 구성.
- 실측 지형 파악: pytest·sklearn·networkx·polygon·tavily 로컬 설치 후 4개 서브시스템 전수 실행.
  - 처음: 루트 tests/ 1 fail, ingest2 1 fail(수집 에러 12는 cd 위치 문제였고 루트에서 실행 시 해소). backend 170 green.
- 실패 2건 실체 규명 후 수정(둘 다 리팩터 후 낡은 테스트 — 코드가 옳음):
  ①ingest2 test_candidates::test_adapter_maps_core_fields — tickers_mentioned에 간접 티커까지 기대했으나, Event 스키마 계약(tickers_mentioned=직접 언급, tickers_indirect=파급)과 하류(price_reaction·causal.edges가 직접 티커만 사용)가 분리를 요구. 간접을 섞으면 뉴스에 없던 종목 주가반응 오측정·허위 인과엣지. → 테스트를 분리 검증으로 교정 + adapter.py docstring 2곳(낡은 "직접+간접" 서술) 정정.
  ②루트 test_causal_edges::test_candidate_pairs_passes_on_time_proximity — 시간 근접 단독으로 후보쌍 1개 기대(존재하지 않는 time_close 키 검증). edges.py docstring이 명시적으로 "time_close 단독 제거 — 수집창 12~48h면 전 쌍 통과해 LLM 폭발"이라 선언. → 새 설계 검증(시간 근접 단독은 후보 아님)으로 교체 + 미사용 TIME_WINDOW_DAYS import 제거(ruff F401).
- 진짜 CI 조건 검증: .env 2개를 잠시 감추고 API 키 env 전부 unset한 상태로 재실행 → backend 170·src 161·ingest2 93·research_lab 17 = **441 passed, 0 fail**(네트워크·키 없이 결정론 통과 확인). .env 복원.
- 신규 .github/workflows/tests.yml: 3잡 병렬(backend / core[src+ingest2] / research_lab), Python 3.12, pip 캐시, push+PR 트리거, concurrency 취소.
  - backend: pip install -r backend/requirements-dev.txt(런타임+pytest), cd backend && pytest tests app/deep_research/tests.
  - core: pyproject [project].dependencies를 tomllib로 추출→requirements 파일 경유 설치(pip install $(...)는 >=가 셸 리다이렉트로 오인되므로 파일 경유)+pytest, 루트에서 pytest tests ingest2/tests(둘 다 루트 sys.path 요구).
  - research_lab: pip install pytest pydantic, 해당 디렉토리에서 pytest test_comparator.py.
- 검증: YAML 파싱 OK(3잡·트리거 정상), 각 잡의 정확한 pytest 명령을 지정 작업디렉토리에서 재현 전부 green. .github 추적 가능(gitignore 배제 아님). 아직 커밋/푸시 안 함(사용자 승인 대기 — 워크플로는 푸시돼야 실제 실행됨).

### 추가(2026-07-15) - [로드맵→구현] 8워커 → 배치 구조화 (score.py + causal/edges.py)
목적: 무료티어 Gemini RPD 한도가 병목 → 8워커 단건 병렬 호출을 배치 1회 호출로 묶어 호출 수를 배치크기배 절감. 방금 만든 구조화 출력 인프라(response_schema) 연장선.
- 공통 설계(두 사이트 동일 패턴): 배치는 **opt-in**(기존 단건 경로·테스트 보존), 배치 실패·개수 불일치 시 **단건 폴백**(정확성 보존), 배치 간 소폭 병렬(호출이 크므로 워커 축소), 결과 순서 pool.map으로 보존. 전부 주입 가능(오프라인 테스트).
- ingest2/analyze/score.py:
  - `ImpactAnalysisBatch(analyses: list[ImpactAnalysis])` 스키마 + `_build_batch_prompt`(ITEM k 번호매김, EXACTLY n 강제) + `make_gemini_batch_llm`(list[str]→list[ImpactAnalysis], response_schema 강제).
  - `analyze_story` 갱신 로직을 `_apply_analysis` 공용 헬퍼로 추출. `_score_chunk`가 배치 1회 + 개수검증 + 단건 폴백(폴백도 실패 시 원본 유지) 담당.
  - `score_candidates(..., batch_llm_fn=None, batch_size=5)`: batch_llm_fn 주면 batch_size 청크로 스코어, 없으면 기존 8워커 경로. batch_size 5(스토리 프롬프트가 커서 과대배치 시 품질저하 방지).
  - 배선: pipeline_core.py가 make_impact_batch_llm() 주입 → 프로덕션 배치 활성.
- src/causal/edges.py:
  - `_PairVerdict`/`_PairVerdictBatch` 스키마 + `_build_pair_batch_prompt`(PAIR k 번호, EXACTLY n) + `_check_pairs_batch`([(a,b)]→[dict], response_schema 강제).
  - `_process`의 판정→엣지 로직을 `_verdict_to_edge` 순수함수로 추출(단건·배치 공용).
  - `infer_pairwise(..., pair_fn=None, batch_pair_fn=None, batch_size=6)`: batch_pair_fn 기본값=실 Gemini 배치라 **모든 호출부(cli.py·candidates/pipeline.py)에서 자동 배치 활성**(시그니처 하위호환). 배치 실패/개수불일치→pair_fn 단건 폴백, 단건 중 개별 실패는 그 쌍만 스킵.
  - 부수 개선: 기존엔 no-op였던 on_progress를 청크당 콜백으로 실제 호출.
- 검증: 신규 테스트 15개(analyze 8: 배치 호출수 감소·스토리별 매핑·배치실패 폴백·개수불일치 폴백·부분폴백 원본유지·정렬·프롬프트·스키마 / causal 7: 배치 엣지생성·비인과 필터·배치실패 폴백·개수불일치 폴백·단건 개별실패 스킵·후보없음 무호출·프롬프트). 동시 append 순서 비결정성은 순서무관 단정으로 처리(3회 반복 무flaky). ROOT 168·INGEST2 101 전량 green. 프로덕션 배선 임포트 스모크 OK.

### 추가(2026-07-15) - [로드맵→구현] rank/final.py "deadline 토큰" 오탐 수정
문제: `_LEGAL_SOLICITATION_RE`(증권 집단소송 로펌 광고 탐지)의 맨 단어 `deadline`이 정당한 금융 뉴스를 오탐 → -0.25 legal 페널티 + max_legal_solicitations cap을 잘못 적용해 정상 스토리를 Top-N에서 강등/배제. 규제 마감·공개매수 마감·채무 만기·정부 셧다운 deadline 등이 전부 걸림. 동일 버그 클래스인 `losses of`("reported losses of $2B" 실적 뉴스)도 함께 수정.
- 원인: 강한 신호(로펌명·class action·lead plaintiff·shareholder alert 등)는 그 자체로 로펌광고를 특정하지만, `deadline`·`losses of`는 정당 뉴스에도 흔한 약한 토큰인데 맨 단어로 alternation에 들어가 있었음.
- 수정: 약한 토큰을 로펌광고 문맥 구절로 축소 — `deadline` → `deadline reminder`, `losses of` → `losses of (?:more than|over|exceeding|in excess of)`. 기존 테스트의 legal 항목들은 rosen law·class action·lead plaintiff 등 강한 토큰으로 이미 잡혀 재현율 손실 없음(로펌 스팸은 거의 항상 강한 신호 동반). 상수 위에 재추가 방지 주석.
- 검증: 신규 테스트 4개 — ①정당 deadline 4종(SEC filing/tender offer/debt maturity/shutdown) 미탐 ②실적 losses of 2종 미탐 ③로펌광고 deadline reminder·losses of more than 계속 탐지 ④bare deadline 뉴스가 legal 페널티 없이 정상 랭크(엔드투엔드). 기존 legal cap 테스트 유지. ingest2 105 전량 green.

### 추가(2026-07-15) - [로드맵→구현] common.py 공유 유틸 — deep_research 내부 중복 단일화
로드맵 "common/ 공유 패키지"를 실측 기반으로 현실 범위 확정. 크로스패키지(backend↔ingest2↔src)는 sys.path 경계가 달라(빌드시스템 없음, 플랫 레이아웃) 공유 불가·고위험 → **backend/app/deep_research 내부 중복만** 통합. 신뢰도/tier는 이미 source_registry로 단일화됨, 한국어 금액 파서도 numeric_consistency에 중앙화됨(중복 아님) → 제외.
- 신규 backend/app/deep_research/common.py:
  - `domain_of(url)`: urlparse(url).netloc.removeprefix("www.")가 ~13곳에 흩어져 있었고 일부만 .lower()를 붙여 대소문자 불일치 위험. 단일 함수로 통일. **소문자화를 removeprefix보다 먼저** — 'WWW.'(대문자)가 안 벗겨지던 잠재 버그를 신규 테스트가 잡아 수정(기존 흩어진 코드에도 있던 결함).
  - `parse_json_object(text)`: planner/critic/synthesizer에 문자 그대로 복제돼 있던 _parse_json의 단일 소스(코드펜스 제거 → 통짜 파싱 → 첫 {…} 블록 폴백).
- 배선(전부 교체·검증):
  - domain_of ← cross_checker(_domain_weight), evidence_ranker(_extract_domain 래퍼 제거), extractor(3곳: BLOCKED 매칭·정렬·credibility), synthesizer(_build_source_list 2곳), official_source_searcher, pipeline(4곳: 지역 import urlparse 제거). host-only 3곳(accessible_resolver·alternate_finder·jina_reader, www 미제거 의도)은 유지.
  - parse_json_object ← planner·critic·synthesizer의 _parse_json 3벌 제거. 딸려서 미사용된 import re(planner·critic)·import json(critic)도 정리.
- 부수효과: extractor의 BLOCKED_DOMAINS 매칭이 이제 대소문자 무시(이전엔 'WSJ.com' 미매칭 버그).
- 검증: 신규 test_common.py 11개(domain_of 소문자/None/서브도메인/포트, parse_json_object 코드펜스/임베디드/무효). backend 181 전량 green. 임포트 체인·FastAPI 부팅(43 routes) OK. common은 stdlib만 의존 → 순환 임포트 없음.

### 추가(2026-07-15) - [검증] 통합 라이브 E2E — 이번 세션 변경 회귀 확인
목적: 구조화 출력 이식·common 리팩터(domain_of/parse_json_object)·XBRL가 실제 파이프라인에서 함께 깨지지 않는지 라이브로 확인(단위 테스트가 못 잡는 통합 회귀 검출).
- 잡 84e568d6 (INDI, 수치+Wuxi+리스크 종합 질의) → done, error=None.
- 마커 전부 정상 발화, **ERROR/Traceback 0건**:
  - planner 구조화(8쿼리), critic 구조화 ×5 이터레이션, synthesizer 2단계 구조화 추출(findings 3·timeline 6), 자기 검증 패스(구조화), xbrl_ledger 원장 구축(5,148 USD 항목, CIK0001841925), 원장 일치 6건.
- 결과: summary 607자, sections 6, key_findings 3, timeline 6, cross_validation 15(교차검증 + 환율 정합/이상 + 원장 일치). [unverified] 태깅·환율 이상 밴드 탐지도 정상 동작.
- 결론: common 리팩터가 라이브 전 경로(소스 저장·신뢰도·JSON 폴백 파싱)에서 무결. 구조화 출력·XBRL·수치정합 방어가 통합 상태로 함께 작동 확인.
- 범위 주의: ingest2 배치화(score/edges)는 deep_research 파이프라인이 아니라 별도 뉴스 파이프라인 → 이 E2E엔 미포함(주입 단위테스트 15개로 검증됨).

### 추가(2026-07-16) - [확장] 다통화 FX 밴드 — numeric_consistency 환율 검사 RMB/USD → 다통화
배경: 환율 정합/이상 검사가 frozenset(("RMB","USD")) 하드코딩이라 RMB만 커버(Fable·메모 지적). E2E에서도 RMB/USD만 발화 확인.
- 일반화: _FX_BANDS(frozenset 키) → _FX_PER_USD_BANDS(통화코드→'USD 1당 단위' 밴드). USD를 피벗으로 (외화 등가액 ↔ USD 등가액) 쌍만 검사하도록 루프 재작성. 출처간 일관성도 rmb_usd_rates 단일 리스트 → rates_by_cur[통화] 통화별 그룹으로 확장.
- 추가 통화: HKD(7.0~8.3)·JPY(70~180)·KRW(900~1700)·TWD(25~35). 밴드는 실제 변동폭보다 넓게(단위 亿/万 혼동·통화 오표기 같은 '큰 오류'만 잡는 목적). **EUR/GBP는 의도적 제외** — 환율이 1.0 근처라 무관한 두 금액이 우연히 근접할 때 오탐 위험이 크고 亿/万 단위혼동도 안 일어나 가치 낮음. CNY는 파서(_PREFIX_CUR)가 이미 RMB로 정규화.
- RMB 동작·메시지 포맷(환율 정합/이상/상충, {cur}/USD) 완전 보존 → 기존 4개 FX 테스트 그대로 통과.
- 검증: 신규 7개 — HKD/JPY/KRW/TWD 정합(각 실측 함축환율), HKD 10배 단위오류 이상, EUR 제외(무발화), 통화별 출처간 상충(둘 다 밴드 안이지만 9% 이탈). numeric 49 + backend 188 전량 green.

### 추가(2026-07-16) - [확장] CJK 앵커 게이저티어 — 크로스출처 pro-rata 교차언어 엔티티 연결
배경: 크로스출처 pro-rata(검사 4)의 앵커 추출(_ANCHOR_RE)이 Latin/숫자만 잡아 중국어 회사명('无锡' 등)이 앵커가 못 됐다(_ANCHOR_STOP 주석 "가젯티어 전까지"). 외부 API(GLEIF/OpenFIGI)는 네트워크·키 의존이라 불확실 → **오프라인 게이저티어 파일 + 로더**로 확실히 구현.
- 핵심 가치: **교차언어 앵커링** — 중국어 출처의 '无锡'과 영어 출처의 'Wuxi'를 같은 canonical 앵커 'Wuxi'로 정규화해, 서로 다른 언어의 출처가 같은 딜임을 인식(FinVision의 중국 자회사 크로스보더 시나리오 정조준).
- 신규 backend/app/deep_research/data/entity_gazetteer.json: canonical→[별칭(CJK 간체/번체 + Latin)] 형식. 검증된 매핑만 등재(무할루시네이션): Wuxi/无锡·無錫, BYD/比亚迪, SMIC/中芯国际, CATL/宁德时代, NIO/蔚来, XPeng/小鹏, BeiGene/百济神州, PDD/拼多多, Alibaba·Tencent·Baidu·JD 등 12개. GLEIF/OpenFIGI/Wikidata로 같은 형식 확장 가능.
- numeric_consistency.py: _load_gazetteer(프로세스 1회, 파일 없음/손상 시 조용히 비활성 폴백), _has_cjk(한자/가나/한글 범위), _extract_anchors에 게이저티어 매칭 추가 — CJK 별칭은 부분문자열(공백 없는 CJK 대응), Latin 별칭은 단어경계(alternation 정규식, 긴 별칭 우선). 기존 regex 앵커·_ANCHOR_STOP 완전 보존. 모듈 로거 신설.
- 검증: 신규 7개 — CJK 간체/번체→canonical, 교차언어 동일 canonical(比亚迪↔BYD), 보일러플레이트 CJK 오탐 0, Latin 변형(Pinduoduo→PDD) 정규화, 일반 대문자 stopword 여전히 제외, **교차언어 pro-rata 통합**(중국어 부분출처 40%$80M ↔ 영어 전체출처 100%$200M을 공유앵커 Wuxi로 연결). numeric 56 + backend 195 전량 green. 파일 경로는 __file__ 기반이라 CWD 무관(루트/backend 양쪽 로드 확인). 커밋 시 신규 data/ 파일 포함 필요.

### 추가(2026-07-16) - [확장] 이미지 PDF OCR — 로컬 2단 PDF 추출 (텍스트레이어 → 스캔 OCR)
배경(Fable brief §5-4): 추출이 Jina Reader 전적 의존이라 스캔 이미지 PDF(중국 공시 cninfo/SZSE 구형)는 빈손. PaddleOCR은 py3.14 휠 부재로 배제 → **pypdfium2(텍스트레이어+렌더링) + rapidocr-onnxruntime(중국어+영어 ONNX 내장, 시스템 바이너리 불필요)** 조합을 실측 설치 확인 후 채택.
- 신규 sources/pdf_extractor.py: is_pdf_url(경로 확장자, 쿼리 무시) / extract_pdf(다운로드 20MB 캡·%PDF 매직 확인·to_thread) / _extract_pdf_bytes — ①pypdfium2 텍스트레이어(40p 캡) ②페이지당 평균 60자 미만이면 이미지 PDF 판정 → RapidOCR 폴백(8p 캡·2.0배율 렌더, 페이지 실패 개별 스킵). OCR 엔진 프로세스 싱글턴(실패 시 영구 비활성). 모든 실패 None(호출부 Jina 폴백) — 파이프라인 불사.
- 라이브 실측이 잡은 결함: **CJK word_count 전멸** — 중국어는 공백이 없어 split() 기준 word_count가 한 자릿수 → extractor의 word_count>50 필터에서 OCR 결과가 전량 탈락할 뻔. _word_count(공백 단어 + CJK 문자수)로 수정, 스모크로 124>50 통과 확인.
- 배선 extractor.extract_from_results: PDF URL 분리 → 로컬 추출 우선, 실패분은 Jina에 재시도(폴백 사슬). 기존 웹 경로 무변경.
- 기능 실측(오프라인): 수제 텍스트레이어 PDF → text-layer 경로 175자 ✓. PIL로 그린 중국 공시 스캔 PDF(10줄) → **실 RapidOCR로 960,834,355/135/34.3769%/无锡 전부 인식(227자)** — OCR 텍스트가 그대로 수치정합(FX·pro-rata)·게이저티어 앵커(无锡→Wuxi)로 연결되는 사슬 성립. 라이브(네트워크): Berkshire 2023 letter PDF 7,002단어 추출 ✓.
- requirements.txt: pypdfium2>=4.30, rapidocr-onnxruntime>=1.3, Pillow>=10.0 (rapidocr 미설치여도 텍스트레이어는 동작).
- 검증: 신규 test_pdf_extractor.py 12개 — is_pdf_url, 텍스트레이어 충분 시 OCR 미호출, 이미지 PDF→fake OCR 폴백, CJK word_count 필터 통과, 엔진 폭발/부재 시 None 강등, 쓰레기 바이트 None, 배치 실패 필터. backend 207 전량 green, 부팅 43 routes.

### 추가(2026-07-16) - [확장] 미러 탐색 — Wayback CDX 전략 추가 (accessible_resolver 2차 전략)
조사(4후보 실측): CDX=키 불필요·라이브 확정 / akshare=py3.14 설치+cninfo 공시 라이브 조회 성공(후속 후보) / Brave·Exa=키 미보유로 보류. CDX부터 구현(사용자 지시).
- 문제: 페이월 복구가 Wayback available API 단일 전략 — 'closest' 1개만 주고, **아카이브가 있어도 빈 결과를 주는 URL이 존재**(라이브 실측). 추적 파라미터 붙은 URL(검색결과에서 흔함)도 정확매칭 실패.
- 구현: find_accessible_url 체인 available→CDX. _cdx_snapshot — CDX 인덱스에서 statuscode:200 필터 + limit=-3(음수=최신 N, 정확 URL 응답은 시간 오름차순임을 라이브로 확정) → 마지막 행=최신 200 스냅샷 → web.archive.org/web/{ts}/{original}. 정확 URL 빈손 + 쿼리스트링 존재 시 경로만으로 1회 재시도(_cdx_url_variants). **와일드카드 prefix 매칭은 의도적 배제** — '다른 기사'를 줄 위험(무할루시네이션). 모든 실패 None.
- 라이브 증명: WSJ 기사 + '?mod=hp_lead_pos1'(추적 파라미터) → available 빈손 → **CDX가 쿼리 제거 변형으로 회수(method=cdx)**. 정확 URL은 체인 1단(wayback)이 그대로 처리(순서 보존).
- 검증: 신규 test_accessible_resolver.py 10개 — 체인 순서(available 성공 시 CDX 미호출), 최신 행 선택, 쿼리 변형 재시도(+호출 순서), 쿼리 없으면 1회만, 헤더만/기형 행/비리스트 JSON/전실패 None, available 실패에도 CDX 시도. backend 217 전량 green.
- 남은 미러탐색 후보: akshare cninfo 공시 소스(조사 완료·구현 대기 — PDF OCR과 직결), Brave/Exa(키 필요).

### 추가(2026-07-16) - [확장] cninfo 공시 소스 (akshare) — 미러 탐색 중국 축
웹 검색이 놓치는 중국 A주 공시 원문을 거래소 공시 플랫폼(巨潮资讯)에서 목록 API로 직접 수집. PDF OCR·CDX에 이은 미러탐색 마지막 구현 축(Brave/Exa는 키 필요로 보류).
- 신규 sources/cninfo_disclosure.py: A주 6자리 코드 추출(00/30/60/68/8 프리픽스만 — '19'/'20' 시작은 연월 오탐 방지 제외, B주 200xxx 희귀 트레이드오프 명시) → akshare stock_zh_a_disclosure_report_cninfo(18개월, 코드당 캡) → 공시 제목의 쿼리 토큰(Latin 3자+ · CJK 2그램) 겹침 랭킹(무매칭 시 최신 5) → **정적 PDF URL 변환**(finalpage/{announcementTime}/{announcementId}.PDF — detail 링크 쿼리 파라미터 파싱, 패턴 라이브 실측 확정 92KB %PDF).
- 보수적 활성 조건: 쿼리/컨텍스트(cn_ticker)에 A주 코드가 있을 때만 — 회사명만으로 검색해 잘못된 종목 공시를 '원문' 주입하는 사고 차단(무할루시네이션). akshare 미설치/실패는 빈 결과·코드 스킵(지연 임포트+to_thread — 임포트 수 초).
- 배선: official_source_searcher.search에 CN 관할(primary/secondary) 시 병합, cninfo.com.cn을 searched_domains에 기록(커버리지 반영). 반환 PDF URL은 추출 단계의 로컬 PDF 2단 추출(텍스트레이어→OCR)과 직결.
- 라이브 전체 사슬 증명: akshare 조회(000001) → 키워드(分红/权益) 랭킹 공시 3건 → 정적 PDF URL → **pdf_extractor가 실제 공시 본문 1,129단어 추출**(平安银行 권익분파 공고, 텍스트레이어 경로).
- 검증: 신규 test_cninfo_disclosure.py 14개 — 코드 추출(프리픽스/연월 오탐/중복), PDF URL 파싱(폴백/무효), 랭킹(키워드/최신 폴백), search(코드 없으면 무조회, context 코드, 조회 실패 스킵, akshare 부재, 코드 캡). backend 231 전량 green, 부팅 43 routes. requirements에 akshare>=1.18 추가.

### 추가(2026-07-16) - [검증→수정] 크로스보더 종합 라이브 E2E (BYD 002594) — 치명 크래시 1 + 변환 버그 1 발견·수정
§6 "종합 라이브 최종 검증 미완" 해소 목적. 신형 사슬(CN관할→cninfo→PDF→FX/앵커)이 파이프라인 안에서 함께 도는지 BYD(게이저티어 등재·실존 A주)로 검증 → 1차 실행에서 진짜 결함 2개가 드러남.
- **치명 1: PDFium 동시 호출 프로세스 즉사** — 1차 잡이 PDF 배치(동시 3) 파싱 시점에 uvicorn이 Python 트레이스백 없이 사망(SZSE PDF 다운로드 200 직후). PDFium은 스레드 안전하지 않아 동시 파싱/렌더 시 네이티브 액세스 위반. → `_PDFIUM_LOCK`(threading.Lock)으로 네이티브 구간(파싱→텍스트→렌더→OCR) 전체 직렬화, 다운로드 I/O는 병렬 유지. 검증: 크래시 지점의 실제 SZSE PDF 포함 6건 동시 3 스트레스 → 6/6 성공·프로세스 생존.
- **버그 2: cninfo announcementTime 형식 편차** — 002594 링크는 '2026-07-02 00:00:00'(시간 포함)이라 YYYY-MM-DD fullmatch 탈락 → 선별 7건 전량 병합 실패(000001은 우연히 날짜만이라 첫 라이브에서 안 걸림). → [:10] 정규화. 회귀 테스트 추가(시간 포함 형식). 라이브: BYD 7건 변환 + 공시 PDF 1,826단어 실추출.
- **2차 E2E 완주(잡 fd9cfdf5)**: done·오류 0·서버 생존. 소스 286개. 마커 — PDF text-layer ×15(+크기캡 차단 ×3), wayback 회수 ×5, cninfo 선별(225→7), 구조화 planner/critic×5/추출/자기검증 전부. cross_validation 12건: **[pro-rata 정합] 8,142.70万元≈81,910.64万元×10% (RMB)** + **[환율 정합] ×5**(¥804B≈$116B 등 실제 BYD 수치) — 중국어 공시 수치가 결정론 검사를 실전 관통. 1차 실행에선 야생 스캔 PDF(미래에셋) OCR 5,106자도 확인.
- backend 232 전량 green(회귀 1 추가). 참고: 일시적 cninfo 네트워크 플레이크는 설계된 조용한 폴백으로 처리됨(재시도서 정상).

### 추가(2026-07-16) - [감사] 리뷰 범위 밖 영역 감사 + GitHub Actions 실green 확인
사용자 지시로 잔여 영역 순차 처리(finvision_crawling은 사용자가 로컬 제거 예정으로 제외).
- **CI 실green**: 비인증 GitHub API로 c44aadc 체크런 조회 — backend/core/research_lab 3잡 전부 completed/success. 회귀 방지 실가동 확인.
- **프론트 API 계약 전수 대조 (불일치 0)**: api/index.js 24개 호출(macro 5·stock 16·portfolio 3) ↔ app.routes 전수 매칭. deep research 인라인 호출(세션 CRUD 5종·plan/refine/execute/chat/status/stream)도 라우터에 전부 존재. SSE 이벤트 형태(stage/progress_pct) = pipeline emit 직렬화와 일치, 완료 처리(done→status 재조회→result 렌더)·error 스테이지 처리 확인. ResearchReport 소비 필드(sections/key_findings/timeline/coverage/cross_validation/unverified_gaps/metadata.total_queries·total_sources·elapsed_seconds·estimated_cost_usd) 전부 모델에 존재.
- **ui/ (Next.js)**: /today 단일 페이지, 백엔드 API 호출 0 — data/stories_latest.json 정적 읽기 전용(구조적 저위험).
- **research_lab/gemini_langfuse_log_runner**: 격리 실험 러너(1,920줄, 서비스 무접촉). .env 미추적·전 히스토리에 커밋 이력 없음. 추적된 output/ 산출물 14개에 키 패턴(AIza…/sk-…/api_key=) 없음.
- 결론: 잔여 영역에서 실결함 0. 미해결로 남는 것: Brave/Exa(키 필요·선택), 보안 권고 중 사용자 보류분(.env 키 로테이션 판단).

### 추가(2026-07-19) - [종결] 보안 권고 처리 완료 — Fable 리뷰 사이클 전 항목 종결
- .env 키 로테이션: **사용자 결정으로 미실시**. 근거: 실키 포함 zip은 Fable 챗봇 검토 1회 업로드 외 공유 이력 없음(저장소 히스토리 유출 없음은 기확인). 노출 경로 제한적.
- finvision_crawling: 사용자가 일회성 코드로 확인, 로컬 정리 예정 — 감사 범위 제외.
- 이로써 Fable 리뷰 발 전 항목(즉시수정·로드맵·확장·종합 E2E·잔여영역 감사·보안 권고) 종결. 남은 선택지: Brave/Exa 키 발급 시 배선(비차단).

### 추가(2026-07-19) - [UX/성능] 대시보드 무한로딩·주가 표시 수정 (사용자 불편 신고 2건)
- **FRED 403 차단**: FRED CDN(Akamai)이 기본 python UA·연발 호출을 403으로 차단(실측: 첫 200 → 연발 후 IP 일시차단). fred_client에 브라우저형 UA + 시리즈별 10분 캐시(+실패 시 만료 캐시 폴백). 키 주입(.env FRED_API_KEY) 후 지표 12/12 실데이터 확인.
- **market-state 120s+ 행**: business_cycle이 FRED 일간 시리즈를 2만행×12개 직렬 수집 → 프론트 60s 타임아웃 → "분석 중..." 무한. 수정: 패턴 캐시를 디스크(backend/data/cycle_cache.json, 24h TTL, data/는 gitignore)로 2단화 + startup 백그라운드 예열(warm_cache_async) + 중복 발사 방지 락. 실측 120s+ → **0.22s**.
- **현재가 커서로만 보임**: overview(무거움·타임아웃 가능)가 실패하면 헤더 전체가 안 그려져 차트 호버로만 가격 확인 가능했음. StockDetail 수정: quote를 마운트 즉시 1회+30s 주기로 독립 조회, 헤더 현재가는 quote 우선, **전일 대비 등락(±금액/%) 신설**, 로딩 스켈레톤·overview 실패 시에도 현재가 헤더 표시. 브라우저 검증: TSLA $380.84 · 전일 대비 -10.22(-2.61%) 즉시 표시, 콘솔 무오류.

### 추가(2026-07-19) - [버그] 리서치 채팅 프롬프트 증발 수정 (사용자 신고)
- **근본 원인**: init_db가 research_sessions/research_messages 테이블을 생성하지 않아(누락) 세션 API 전체가 500(`no such table: research_sessions`). 프론트 핸들러가 `ensureSession → addMsg(user)` 순서라 세션 생성 throw 시 사용자 메시지 추가 전에 죽음 — 입력은 이미 비워져 프롬프트 증발.
- **백엔드**: database.py init_db에 research_sessions/research_messages CREATE TABLE IF NOT EXISTS + 세션·시간 인덱스 추가(라우터 컬럼과 정합 확인). curl 검증: 세션 생성/목록 200.
- **프론트**: (1) addMsg(user)를 ensureSession보다 먼저 — 메시지 즉시 표시, (2) ensureSession 실패를 non-fatal로(null 반환, 저장만 스킵하고 채팅 계속), (3) saveMsg sid null 가드.
- **브라우저 E2E**: TSLA 심층 채팅에 질문 전송 → 프롬프트 유지 + POST /sessions·/messages·/plan 전부 200 + 리서치 계획 렌더링(실제 사전검색 근거 포함) 확인.

### 추가(2026-07-19) - [개선] 리서치 계획 2라운드 정찰 — 사고 깊이 강화 (사용자 피드백 "사고가 짧다")
- 기존: 광각 쿼리 5개 1회 검색 → 바로 계획(16.5s 실측). 표면 정보 기반이라 얕음.
- 변경: chat_service._scout_search를 2라운드로 재구성 — ① 1차 광각 정찰(5쿼리) → ② SCOUT_FOLLOWUP_PROMPT로 빈틈 분석(1차 결과의 구체 사건 원문 확인·미검증 주장·미커버 측면·반대관점) → ③ 추적 쿼리 4개 2차 정찰 → ④ 라운드 구분 표기로 종합해 계획 작성. 검색 러너는 _run_search_round로 추출(Parallel 배치+Tavily 병렬 동일), _gen_queries/_dedup_lines 헬퍼 분리. 1차 빈손이면 2차 스킵.
- 실측(INDI 동일 질문): 27.0s, 2차 쿼리가 1차 발견을 파고듦("EXALOS/GEO/ams OSRAM 인수 효과", "profitability roadmap", "ADAS/LiDAR 경쟁우위"). 계획 품질: 명칭 교정(indi 세미 컨덕터→indie Semiconductor, 근거 포함) + M&A 세부 + 반대관점(내부자 매도) 포함 확인.
- 비용: 플랜당 lite 호출 +1, tavily +4, parallel 배치 +1 — 무료티어 여유 내.

### 추가(2026-07-19) - [개선2] 딥 플래닝 파이프라인 — N라운드 정찰 + 심사 패스 (사용자 "타 AI는 몇 분씩 사고" 피드백)
- 2라운드 고정 → **N라운드 루프(기본 4, PLAN_SCOUT_ROUNDS env)**: 각 라운드가 누적 증거+기검색 쿼리를 보고 빈틈 분석 → 추적 4쿼리. 모델이 SATURATED 선언 또는 신규 결과 0이면 조기 종료(시간 낭비 자체 방지). 증거 상한 라운드 3500자/전체 11000자.
- **심사 패스 신설(PLAN_REVIEW_PROMPT)**: 초안을 증거와 대조해 심사 — ①질문 측면 커버리지 ②항목별 무엇을·어디서·어떻게 구체성 ③반대가설/리스크 검증 항목 ④증거 없는 추측 제거 ⑤중복 병합·근거 표기 보완 → "심사 반영" 요약과 함께 최종본 출력(사용자에게 사고 과정 가시화). 심사본이 초안의 60% 미만 길이면 초안 유지(생성실패 가드). 검색 증거 없으면 심사 스킵(추측 증폭 방지).
- 실측(INDI, 3라운드+심사): **55.3s**, 증거 10,080자. 3차 쿼리가 "Tier 1 OEM design wins"·"특허 벤치마킹"·"M&A 통합의 마진 영향"·"내부자 매집 추이"까지 파고듦. 심사 반영 확인: "호재 질문이지만 리스크 균형 배치, 막연한 내부자거래 항목을 지배구조 확인으로 구체화, 전 항목에 *확인 방법* 추가". 기본 4라운드로 ~1.5분대.
- 비용: 플랜당 lite ≤5 + flash 2 호출 — 무료티어 여유 내.

### 추가(2026-07-19) - [수정] US 단일 관할 공식소스 집중 검색 활성화 (INDI 실측 "공식쿼리 0" 해소)
- pipeline의 `primary != "US" or cross_border` 게이트 제거 — 가장 흔한 케이스(미국 종목)에서 SEC EDGAR·IR 1차 자료 직접 타격이 빠지던 문제. 이제 전 관할에서 공식소스 검색 실행.
- multilingual_query_builder._add_us_queries에 IR·보도자료 쿼리 신설: `"{회사명}" investor relations press release {이벤트키워드}` (IR 도메인은 사전 미상이라 site: 없이 — businesswire/globenewswire/IR 페이지 타격).
- 라이브 검증(US 단일, INDI): 공식 쿼리 2개 → **10건 수집** — sec.gov EDGAR 실공시(CIK 1841925, 10-Q indi-20260508.htm 포함) + **investors.indie.inc**(신설 IR 쿼리가 회사 IR 사이트 직접 포착) + nasdaq. backend 232 전량 green.
- 참고: Form 4 전수조사는 채팅 execute 경로에서 계획 전문이 query에 포함돼 키워드 트리거 정상(제너릭 엔드포인트 단독 실행에만 미포함이었던 것 — 버그 아님).

### 추가(2026-07-19) - [수정] 커버리지 판정 topic 필터 — 종목 질문에 거시 기관 기대치 제거
- 원인: build_coverage_info가 질문 성격 무관하게 관할의 전 tier-1 도메인(연준·재무부·BLS·BEA·FRED 포함)을 기대치로 잡아, 종목 질문 커버리지가 항상 "미확인 다수"로 표시(INDI 실측 확인 2/미확인 8 — 미확인 대부분이 거시 기관).
- 수정: topic 파라미터 신설 — "company"면 기대 도메인을 category∈{regulator, exchange}로 한정(공시가 사는 곳), "all"(거시)은 기존 유지. pipeline이 컨텍스트 티커 또는 관할 시그널의 ticker:/ctx_ticker: 감지로 topic 결정(제너릭 실행도 커버).
- 검증: US 종목 → 확인 1/1(sec.gov, 미확인 0). US+KR 종목 → sec.gov·dart 확인/fsc·krx만 미확인(거시 기관 부재). 거시(all) → 연준 등 기대 유지 + "(searched, no result)" 라벨 회귀 확인. 신규 test_coverage_info.py 4케이스, backend 236 전량 green.

### 추가(2026-07-19) - [개선3] 딥 플래닝 실시간 진행 표시 (남은 목록 3번)
- 배경: 계획이 1~2분 걸리게 됐는데 프론트는 "사전 검색 중..." 정적 한 줄 — 체감 대기 나쁨.
- 백엔드: PlanRequest에 progress_id(클라 생성) 신설. chat_service.generate_plan/_scout_search에 progress_cb 배선 — 단계별 보고("1차 정찰: 쿼리 생성/광각 검색 실행", "N차 추적: 빈틈 분석/검증·반대관점 검색", "초안 작성", "심사관 패스"). router에 _plan_progress 딕셔너리(+100개 가드, 완료 시 pop) + GET /plan-progress/{id} 폴링 엔드포인트.
- 프론트: handleDeepQuery가 crypto.randomUUID()로 pid 생성해 요청에 포함, 2초 간격 폴링으로 마지막 메시지를 "🔎 {단계} (딥 플래닝 1~2분 소요)"로 갱신. 폴링 실패는 무해(부가 기능), finally에서 타이머 정리.
- 브라우저 E2E 검증: TSLA 로보택시 질문 → "🔎 2차 추적: 검증·반대관점 검색 실행 중 (4개 쿼리)" → "🔎 4차 추적..." 실시간 전환 → 심사반영 포함 최종 계획 도착 확인. backend 236 전량 green.

### 추가(2026-07-19) - [개선4+버그2] 계획 사고 깊이: thinking 모델 승격 + Tavily 432 로테이션 수정 (사용자 "사고 시간 짧음" 지적)
- **본질 진단**: 타 AI의 '몇 분 계획'은 추론 모델의 내부 사고 시간. FinVision 계획 경로는 전부 flash-lite 비추론 호출이라 짧았음. 실측: gemini-3.5-flash가 무료티어에서 thinking 기본 활성(짧은 프롬프트 사고토큰 ~1k, 6s/콜), pro 계열은 429 불가.
- **승격**: llm_client에 generate_text(thinking_budget 지원, 사고토큰 로깅) 신설. 계획 초안·심사 2콜을 PLAN_THINKING_MODEL(기본 gemini-3.5-flash, budget 16384)로 — 실패 시 기존 lite 폴백. 쿼리 생성은 lite 유지(쿼터 절약).
- **버그: Tavily 432 미처리** — 월간 소진 키가 429가 아닌 432 반환(라이브 실측). 로테이션 조건이 (429,402)뿐이라 예비 키 2개가 멀쩡한데 첫 키에서 중단 → 정찰 전패. tavily_search+chat_service 양쪽 (429,402,432)로 수정. 수정 후 로그로 키 전환 확인.
- **정직 강등**: 정찰 전패(크레딧 소진 등) 시 근거 없는 계획을 꾸미지 않고 내부 데이터 모드 강등 + 계획 상단 "⚠️ 웹 정찰 실패" 경고 명시.
- 재실측(INDI): 49.0s — thinking 초안(사고토큰 1,840)+thinking 심사(1,297) 완주, 초안 1,827자→최종 3,678자. **당일 자원 현황: Parallel 402 크레딧 소진, Tavily 3키 중 대부분 소진(15분 쿨다운 복귀)** — 오늘 대량 테스트 영향, 코드가 아닌 쿼터 문제로 기록. backend 236 green.

### 추가(2026-07-19) - [준비] Gemini 유료 전환 코드 — 모델 승격 + 실비용 전수 집계
- **모델 라우팅(.env)**: 사고 필요 단계 전부 gemini-3.5-flash(thinking) 승격 — 플래너(서브쿼리)·비평가·종합·자기검증·계획(초안/심사)·채팅. 대량 작업은 lite 유지: 추출(EXTRACT_MODEL=3.1-flash-lite 명시), 가이던스·stock_profile·ai_client는 자체 lite 고정이라 무영향. 쿼터 소진 시 llm_client fallback_model로 lite 강등(기존 메커니즘).
- **실비용 전수 집계(llm_client)**: _record_usage가 generate_structured(폴백 모델 구분 포함)/generate_text의 usage_metadata(입력·출력·사고 토큰)를 모델별 누적. 2026-07 공식 단가표(_PRICE_PER_M: 3.5-flash $1.5/$9, 3.1-flash-lite $0.25/$1.5, 3.1-pro $2/$12 등, 사고=출력 과금) 기반 estimated_cost_usd()/total_tokens()/reset_usage(). 한계 명시: 레거시 SDK 직호출 폴백 경로는 집계 밖(정상 경로 하한값).
- **파이프라인**: 잡 시작 reset_usage → 중간 비용 가드(MAX_COST_USD_PER_RUN $2)를 전수 집계 기반으로 교체(기존 planner+synth 출력토큰만·구식 단가) → 종료 시 metadata.estimated_cost_usd/gemini_tokens_used 실집계 기록 + 모델별 내역 로그.
- 검증: 신규 test_llm_usage.py 5케이스(모델별 누적, 사고=출력 과금 수기검산, 미등록 모델 lite 근사, reset, usage_metadata 부재 안전) + backend 241 전량 green. 라우팅 해석 실확인(플래너/비평가/종합/검증/계획/채팅=3.5-flash, 추출=lite). 서버 반영.
- 대기: 사용자 유료 키 수령 → .env 교체 → 풀 리서치 1회 시험(품질 + 계산비용 vs AI Studio 실청구 대조).

### 추가(2026-07-19) - [기능] 그라운딩 검색 배선 + 유료 키 전환 — 1차 시험이 드러낸 검색 전멸 해소
- **유료 키 전환**: .env 교체, 스모크로 유료 티어 확인(무료에서 429였던 3.1-pro 응답). 1차 시험(INDI): done, **실비용 $0.3054/54,438토큰/296s** — 비용 예측 적중. 단 Tavily 전키 소진+Parallel 402로 검색 전멸 → 소스 9개, 품질 시험 무효 판정.
- **그라운딩 소스 신규(sources/grounding_search.py)**: generate_content(tools=[google_search]) 1콜 → 모델이 검색어 3~4개 자체 실행(실측) → grounding_chunks(제목=도메인)+grounding_supports(텍스트 조각↔청크 매핑)를 parse_grounding 순수함수로 파싱. **vertexaisearch 리다이렉트 URL을 검색 시점에 실URL 해석**(HEAD→GET 폴백, 동시 8) — 미해석 시 domain_of가 전부 vertexaisearch로 잡혀 신뢰도 랭킹·중복제거 파괴되는 문제 방지. 토큰은 llm_client 집계기 기록, 실행 검색어 수는 모듈 카운터 누적 로그(월 5,000 무료분 추적). 모델 GROUNDING_MODEL=3.1-flash-lite(그라운딩 지원 실측, 토큰비 최소).
- **배선**: searcher._sources에 등록 + _search_one에서 모든 쿼리에 병행 추가(타 엔진 크레딧 소진 시에도 검색 생존). 채팅 스카우트는 라운드당 그라운딩 1콜(검색어 자체 생성 활용).
- 검증: parse_grounding 4케이스(스니펫 매핑·무효청크 스킵·메타 부재 안전·캡) + 라이브 사슬(실URL 해석 — seekingalpha 기사·investing.com Q1 2026 뉴스 회수). backend 245 전량 green. 2차 시험(검색 복구 상태) 실행.

### 추가(2026-07-19) - [기능] 로컬 HTML 추출 폴백 — [[unverified]] 남발의 근본 원인(추출 실패) 대응
- 2차 시험 진단: 소스 144건인데 Jina(무키 20RPM) 503 폭주로 본문 추출 대량 실패 → 자기검증이 근거를 못 찾아 [[unverified]] 175개 남발(실사실까지 미확인 처리).
- 신규 sources/html_extractor.py: httpx+trafilatura 로컬 추출. **2단 UA**(브라우저형 → 403 시 정직한 봇 UA 재시도 — 위키미디어류의 'python TLS+브라우저 UA=스푸핑' 차단 우회, 실측 위키피디아 403→성공). Cloudflare/Akamai TLS 지문 차단 사이트는 로컬 불가로 Jina 몫 명시. 200자 미만/비HTML/3MB 초과 스킵, trafilatura 미설치 시 모듈 비활성.
- extractor 배선: PDF 로컬 → Jina → **Jina 실패분 로컬 HTML 흡수** 3단 체인. 실측 3/3(위키·stockanalysis·marketbeat). backend 245 green, requirements에 trafilatura 추가.
- 오늘 시험 지출 누계: 1차 $0.31(검색 전멸·무효) + 2차 $0.43(검색 부활 144소스, 추출 병목 발견) = **$0.74**. 3차(추출 복구 검증)는 사용자 예산 확인 대기.

### 추가(2026-07-19) - [판정] 3차 시험 — 유료 구성 최종 검증 (3회 누계 $1.09)
- 3차(추출 3단 체인 복구): done, **$0.347/88,513토큰/835s**, 쿼리 61 → **소스 150/도메인 57종**.
- **검증 오염 해소 확인**: 스팸성 [[unverified]] 175→0. 잔존 단일 [unverified] 109개는 성격 전환 — 2차엔 'Fabless' 같은 상식 용어까지 태그, 3차엔 수주잔고 시점·전환사채 등 실검증 필요 주장에만. **GlobalFoundries 파트너십이 미확인→검증([2]) 전환**이 추출 복구의 직접 증거. $7.4B 백로그 최신 수치 포착(2차는 2021년 $2.6B 낡은 값).
- 3회 비교: 1차 $0.31/소스9(검색전멸) → 2차 $0.43/소스144(추출병목) → 3차 $0.35/소스150(정상). **회당 실비용 $0.35 안정 — 본격 사용 가능 판정.**
- 남은 선택 개선: Jina 무료 키(20→200RPM, 강성 차단 사이트 추출) — 사용자 액션 대기. [unverified] 표시 UX(프론트 각주화)는 추후.

### 추가(2026-07-20) - [설정] Jina API 키 적용 — 강성 차단 사이트 추출 활성
- 사용자 Jina 무료 키(가입 토큰 1,000만) .env 등록. jina_reader는 이미 Bearer 헤더 지원(코드 무변경).
- 검증: 로컬 추출로 403이던 reuters.com(1,817단어)·seekingalpha.com(3,557단어) Jina 경유 추출 성공. 한도 분당 20→200회 — 2차 시험의 503 폭주 원인 해소.
- 추출 스택 완성: PDF 로컬(텍스트레이어→OCR) → Jina(키, 200RPM, 강성 사이트) → 로컬 HTML(trafilatura, 2단 UA).

### 추가(2026-07-20) - [기능] 수치 결정론 재검증(방어선 5.5) + 검증 컨텍스트 확대 — [unverified] 마지막 마일
- 4차 진단: 태그 대상이 전부 수치·연도(회사·파트너십은 검증 통과) — 원인은 자기검증 컨텍스트가 원문 10k자/리포트 8k자로 잘려, 수치가 원문에 있어도 컨텍스트 밖이면 미확인 처리.
- **① 검증 컨텍스트 확대**: 원문 10k→150k자, 리포트 8k→30k자 (thinking 모델 유료 전환으로 +$0.07/run 감내 가능).
- **② 신규 agents/numeric_reverifier.py — 수치 전용 결정론 재검증 (LLM 0콜)**: [unverified] 태그 구간의 수치를 **전체 수집 원문(무제한)**과 대조. numeric_consistency 다국어 파서 재사용("74억 달러"↔"$7.4 billion" 교차언어 정규화, 통화 일치+1% 오차), 연도(한글 인접 \b 실패 → 숫자경계 정규식으로 수정), %(0.1%p), 통화없는 큰 수(억/만 조합·콤마수). **보수성 원칙**: 수치 전수 확인 시에만 해제, 부분 확인·수치 없는 사실관계 태그는 유지. synthesizer의 자기검증 직후 배선(방어선 5.5), 실패 시 태그 유지 폴백.
- 검증: 신규 테스트 9케이스(교차언어 매칭, 부분확인 거부, 사실관계 불변, 이중괄호 변형, 무코퍼스 no-op) + backend 254 전량 green. 5차 시험으로 효과 측정 중(기준: 3차 109/4차 114).

### 추가(2026-07-20) - [판정] 5차 시험 — [unverified] 114→1, 마지막 마일 완결
- 5차(컨텍스트 확대+결정론 재검증): done, $0.3392/97,343토큰/869s, 소스 121/도메인 59종. **[unverified] 3차 109/4차 114 → 1개.**
- 남은 1개 검증: "Seeing Machines·emotion3D 포트폴리오 통합" — 수치 없는 사실관계 주장으로, 재검증이 손대지 않는 영역(보수성 원칙 정확 작동). 수치·연도류 태그는 전멸.
- 5회 시험 누계 $1.70(약 2,600원). 유료 전환 프로젝트(모델 승격→비용 집계→그라운딩→Jina→추출 3단→검증 마지막 마일) 완결 판정.

### 추가(2026-07-24) - [분석/검증] 현재 코드 및 심층 리서치 구현 브리핑
- 목적: Claude 작업 이후 현재 저장소 구조와 심층 리서치 실행 흐름을 코드 기준으로 재점검.
- 읽은 핵심 위치: `backend/app/deep_research/` 전체, `backend/app/main.py`, `backend/app/database.py`, `backend/app/api/stock.py`, `frontend/src/components/shared/StockResearchChat.jsx`, `frontend/src/components/shared/StockDetail.jsx`.
- 확인 내용:
  - 계획 생성/수정/승인 후 실행하는 사용자 승인형 흐름.
  - 관할 감지 → 다중 검색원 병렬 검색 → 공식 소스/SEC/IR/시장 데이터 보강 → 전문 추출/페이월 복구 → Discovery 단서 추적 → Critic 반사 루프 → 보고서 생성/교차검증/수치 재검증/정제 순서.
  - 비동기 작업 상태는 프로세스 메모리, 진행 이벤트는 SSE, 채팅 세션/메시지는 SQLite에 저장.
  - `.env`는 값 자체를 읽거나 기록하지 않고 `has_key`만 확인: Gemini/Google/Tavily/Parallel/FRED=True, SEC_USER_AGENT/DART=False.
- 수정 파일: `CODEX_WORKLOG.md`만 추가 기록. 제품 코드는 수정하지 않음.
- 검증:
  - `cd backend; python -m pytest -q` → `322 passed`, 경고 57개.
  - `cd frontend; npm run build` → 성공, 2695 modules transformed.
  - 빌드 경고: `NewsFeed.jsx`, `StockResearchChat.jsx`의 동적/정적 import 혼용으로 별도 chunk 분리 안 됨. `recharts` chunk 547.71kB로 500kB 경고.
- 남은 리스크:
  - 실제 외부 API 풀 리서치는 비용과 쿼터를 사용하므로 이번 점검에서 실행하지 않음.
  - 작업/결과가 메모리 기반이라 서버 재시작 시 진행 중 job 및 완료 결과가 사라지고, 다중 worker 간 공유되지 않음.
  - `_plan_progress`도 메모리 폴링 구조라 재시작/다중 worker에 취약.
  - `research_messages`에 DB foreign key 제약이 없어 세션 삭제/비정상 호출 시 고아 행 방지는 애플리케이션 로직에 의존.

### 추가(2026-07-24) - [FCF-03 최소 구현] Claim Safety Ledger
- 목적: ChatGPT 심층 리서치 비교 지적 중 가장 큰 구조적 공백인 “사실·전망·추론 미분리”, “최종 주장과 validator 결과 미연결”, “미검증 주장의 요약 승격 위험”을 DB 마이그레이션 없이 축소.
- 수정:
  - `backend/app/deep_research/models.py`
    - `ClaimRecord` 추가: `claim_id`, `research_run_id`, `claim_text`, `claim_type`, `confidence`, `verification_status`, `source_ids`, `evidence_excerpt`, `counter_evidence`, `executive_summary_eligible`.
    - `DeepResearchResponse.claim_ledger`를 기본 빈 배열로 추가. 기존 응답과 호환되는 additive 변경.
  - `backend/app/deep_research/agents/claim_ledger.py`
    - 최종 `key_findings`만 입력받는 결정론적 원장 생성기 추가.
    - `source_matcher`와 `cross_checker`를 재사용해 `verified`, `single_source`, `partially_verified`, `contradicted`, `unverified` 판정.
    - `[추론]`, `[unverified]`, 회사 전망, 외부 전망, FinVision 계산을 구분.
    - run ID와 최종 claim 텍스트 기반 안정적 claim ID 생성.
    - 미검증·상충·FinVision 해석은 Executive Summary 승격 불가. 외부 전망/자체 계산은 high confidence일 때만 승격 후보.
  - `backend/app/deep_research/agents/synthesizer.py`
    - 초안·최종 응답 조립 시 동일 run의 최종 `key_findings`로 claim ledger 생성.
  - `backend/tests/test_claim_ledger.py`
    - run 격리/안정 ID, 근거 연결, 미검증 승격 차단, 추론 분류, 최종 claim만 포함하는 회귀 테스트 4개 추가.
- 수정 이유:
  - 기존 `cross_validation`은 사람이 읽는 문자열 배열이라 어느 claim에 대한 결과인지 구조적으로 연결되지 않았음.
  - `job_id`는 존재했지만 validator 결과 객체에 전달되지 않았음.
  - 중간 수치 validator 결과와 최종 보고서 주장을 구조적으로 구분할 필드가 없었음.
- 검증:
  - `python -m compileall -q app/deep_research` → 성공.
  - 핵심 회귀 32개 → 전부 통과.
  - `cd backend; python -m pytest -q` → `326 passed`, 경고 57개.
- 남은 리스크:
  - claim type은 현재 태그/표현 기반 결정론 분류라 복잡한 문장은 향후 구조화 LLM 출력과 결합 필요.
  - `executive_summary_eligible`는 응답에 제공되지만 기존 자유형 summary 문장을 아직 물리적으로 제거하지는 않음.
  - 계산식·가정·기간·단위·분모를 보존하는 Calculation Ledger는 별도 단계 필요.
  - claim ledger DB 영속화와 프론트 표시도 후속 단계.

### 추가(2026-07-24) - [FCF-03C] 산업 독립 Metric/Calculation Ledger
- 목적: Micron/DRAM 같은 특정 산업용 계산 패치를 금지하고, 모든 기업·산업·거시 계산에 동일하게 적용되는 의미 기반 계산 계약과 validator 도입.
- 수정:
  - `backend/app/deep_research/models.py`
    - 범용 `MetricValue`: 지표명, 값, 단위, entity, scope, period, period_type, as_of, basis, currency, source_id.
    - 범용 `CalculationRecord`: run/calculation ID, 계산 유형, 설명, 산식, 입력, 가정, 정렬 차원, 출력, 검증 결과, 요약 승격 가능 여부.
    - `DeepResearchResponse.calculation_ledger` additive field.
  - `backend/app/deep_research/agents/calculation_ledger.py`
    - 기업명·산업명·지표명 하드코딩 없는 결정론 validator.
    - 계산이 선언한 의미 정렬 차원(entity/scope/period/period_type/as_of/basis/currency)을 범용 비교.
    - `scope_mismatch`, `period_type_mismatch`, `basis_mismatch`, 메타데이터/출처/산식 누락 등을 구조화.
    - forecast/scenario 자동 요약 승격 차단. 검증된 derived/mechanical_sensitivity만 후보.
  - `backend/app/deep_research/agents/synthesizer.py`
    - 구조화 메타데이터 출력에 calculations 추가.
    - 실제 보고서에 존재하는 계산만 추출하고 입력의 의미·범위·기간·기준일·회계기준·단위·통화·출처를 보존하도록 프롬프트/스키마 확장.
    - 초안/최종 응답에 같은 run ID의 calculation ledger 조립.
  - `backend/tests/test_calculation_ledger.py`
    - 반도체·SaaS·항공·에너지 범위명을 동일 validator가 처리하는 scope mismatch 회귀.
    - 분기/연간, actual/guidance, 출처 누락, forecast 자동 승격 차단, run-scoped 안정 ID 검증.
- 설계 원칙:
  - 산업별 공식은 AI가 제안할 수 있으나 검증 코드는 산업을 알지 않는다.
  - validator는 문자열 키워드가 아니라 계산이 명시한 의미 차원을 비교한다.
  - 계산이 없으면 빈 ledger. 보고서에 없는 계산을 새로 만들지 않는다.
- 검증:
  - `python -m compileall -q app/deep_research` → 성공.
  - 핵심 회귀 33개 → 전부 통과.
  - `cd backend; python -m pytest -q` → `332 passed`, 경고 57개.
- 남은 리스크:
  - 현재 계산 추출은 Gemini 구조화 출력에 의존. 런타임 fixture로 추출 품질 검증 필요.
  - 산식 문자열의 실제 산술 재계산 엔진은 아직 없음. 현재 단계는 의미 차원 정합과 추적 가능성 확보.
  - required_alignment 자체가 잘못 선언되는 경우를 잡는 공식별 type system은 후속 단계.

### 추가(2026-07-24) - [FCF-03D/03E] 검색 관측 원장 + 반증/공식공시 fallback
- 목적: 검색 실패를 정보 부재로 뭉개는 문제와 긍정 서사 편향을 산업 독립적으로 축소.
- 수정:
  - `backend/app/deep_research/models.py`
    - `SearchAttempt` 추가: query, source, status, result_count, duration_ms, error_type, message.
    - `DeepResearchResponse.search_attempts`, `ResearchMetadata.counter_evidence_queries` additive field.
  - `backend/app/deep_research/agents/searcher.py`
    - 모든 searcher 제공자 호출을 `_search_source`로 감싸 run-scoped 시도 기록.
    - 상태: `success`, `no_results`, `not_searched`, `timeout`, `access_denied`, `parse_failed`, `provider_error`.
    - 요청했지만 API 키/필수 설정이 없는 소스는 `not_searched/source_unavailable`.
    - `reset()`이 시도 원장도 비워 run 간 누수 차단.
  - `backend/app/deep_research/agents/planner.py`
    - 계획에 최소 2개 반증 쿼리와 `counter_evidence` rationale을 요구.
  - `backend/app/deep_research/pipeline.py`
    - Planner 출력에 의존하지 않는 `_build_counter_queries` 추가.
    - 매 run마다 일반 반대 근거 1개 + SEC 10-K/10-Q Risk Factors/Legal Proceedings/Commitments fallback 1개 강제 실행.
    - ticker/company/query 앵커만 사용하며 특정 기업·산업 키워드 없음.
    - 결과 전문을 기존 추출·페이월 복구·raw source·최종 합성 경로에 병합.
    - 최종 응답에 실제 검색 시도 원장과 반증 쿼리 노출.
  - `backend/tests/test_search_observability.py`
    - 성공/무결과, timeout/access denied/parse/provider error, unavailable, reset 격리, 산업 비의존 반증 쿼리 검증.
- 검증:
  - `python -m compileall -q app/deep_research` → 성공.
  - 핵심 회귀 30개 → 전부 통과.
  - `cd backend; python -m pytest -q` → `337 passed`, 경고 57개.
- 남은 리스크:
  - 일부 개별 source 구현이 내부 예외를 잡고 빈 배열로 반환하므로 그 경우 searcher 외곽에서는 `no_results`로 관측될 수 있음. 제공자별 typed outcome 전환은 후속 강화 필요.
  - SEC fallback은 검색 + 기존 상시 filing timeline 조합. 문서 섹션별 구조 인덱스는 아직 없음.
  - 반증 검색 결과가 존재한다는 것과 특정 최종 claim의 counter_evidence에 연결되는 것은 별개. 현재 Claim Ledger의 cross-check 연결을 향후 더 강화해야 함.

### 추가(2026-07-24) - [FCF-03F/04] 안전 Executive Summary + 원장 UI/영속화
- 목적: 자유형 요약에 미검증·상충·추론 주장이 승격되는 경로를 사용자 기본 화면에서 물리적으로 차단하고, 검증 근거를 사람이 감사할 수 있게 표시.
- 수정:
  - `backend/app/deep_research/models.py`
    - `DeepResearchResponse.safe_executive_summary` additive field.
  - `backend/app/deep_research/agents/synthesizer.py`
    - 최종 Claim Ledger에서 `executive_summary_eligible=True`인 claim만 결정론적으로 안전 요약에 조립.
    - 통과 claim이 없으면 “검증 기준을 통과한 핵심 주장이 없음”을 명시하고 자유형 내용을 대신 노출하지 않음.
    - 기존 `summary`는 전체 보고서 원문 호환성을 위해 보존.
  - `frontend/src/components/shared/StockResearchChat.jsx`
    - 최종본 기본 요약을 `safe_executive_summary`로 전환. 초안은 기존 summary 유지.
    - Claim Ledger UI: 주장 유형, 검증 상태, 요약 승격 여부, 근거 excerpt, 반대 근거, 출처.
    - Calculation Ledger UI: 산식, 검증상태, 오류 코드, 가정.
    - Search Diagnostics UI: 제공자별 success/no_results/실패/미실행과 검색어.
    - 기존 최종 응답 전체 JSON 세션 저장 경로를 그대로 사용하므로 새 원장도 SQLite `research_messages`에 영속화되고 재접속 시 복원.
  - `backend/tests/test_claim_ledger.py`
    - 검증 claim만 안전 요약 후보가 되고 미검증 경쟁사 주장이 제외되는 정책 테스트.
- 검증:
  - `cd backend; python -m pytest -q` → `338 passed`, 경고 57개.
  - `cd frontend; npm run build` → 성공, 2695 modules transformed.
- 남은 리스크:
  - 안전 요약은 현재 key finding 기반 bullet 조립이라 자연스러운 서술성보다 안전성을 우선.
  - Bull/Base/Bear 범용 시나리오 계약·확률/무효화 조건 validator는 다음 작업.
  - 프론트 기존 번들 경고(recharts 547.71kB, 동적/정적 import 혼용)는 유지.

### 추가(2026-07-24) - [FCF-03G] 산업 독립 Bull/Base/Bear 시나리오 계층
- 목적: P/B 단일 판단이나 반도체 전용 모델 대신 모든 기업·산업에 공통 적용되는 시나리오 계약, 검증, UI 도입.
- 수정:
  - `backend/app/deep_research/models.py`
    - `ScenarioCase`, `ScenarioAnalysis`, `DeepResearchResponse.scenario_analysis`.
  - `backend/app/deep_research/agents/scenario_validator.py`
    - Bull/Base/Bear 3개 존재·중복 검사.
    - 확률 0~1/0~100 입력 정규화 및 합계 100% 검사.
    - 각 case의 가정, 결과, 무효화 조건, 근거 출처 필수.
    - 세 case 결과가 동일 metric/unit/entity/scope/period/period_type/currency인지 비교.
    - 하나라도 실패하면 `validation_status=invalid`, `executive_summary_eligible=False`.
    - 산업·기업·지표명 하드코딩 없음.
  - `backend/app/deep_research/agents/synthesizer.py`
    - 투자 전망·밸류에이션·향후 실적 질문에만 시나리오를 작성하도록 지시.
    - 자료 부족 시 수치를 만들지 않고 빈 시나리오 유지.
    - 구조화 출력/자기검증 스키마에 scenarios 추가, 최종 응답 validator 연결.
  - `frontend/src/components/shared/StockResearchChat.jsx`
    - Bear/Base/Bull 확률·결과·가정·무효화 조건·근거 출처 카드.
    - invalid 시 결과 사용 차단 사유 표시.
  - `backend/tests/test_scenario_validator.py`
    - 일반기업 EPS, 은행 순이자이익, SaaS ARR, 항공 영업이익, 에너지 FCF를 동일 validator로 검증.
    - 확률 합계, 결과 scope 불일치, 무효화·근거 누락, 백분율 정규화, 빈 시나리오 비생성 테스트.
- 검증:
  - `python -m compileall -q app/deep_research` → 성공.
  - 핵심 회귀 44개 → 전부 통과.
  - `cd backend; python -m pytest -q` → `348 passed`, 경고 57개.
  - `cd frontend; npm run build` → 성공, 2695 modules transformed.
- 남은 리스크:
  - 시나리오 수치는 구조화 추출된 보고서 값을 검증하며, 독립 산술 재계산 엔진과 직접 연결은 후속 강화 가능.
  - 실제 LLM 런타임에서 다양한 종목에 대한 시나리오 추출 품질은 비용 허가 후 live 회귀 필요.
  - 프론트 기존 번들 경고는 유지.

### 추가(2026-07-24) - [FCF-05 runtime 검증] JPM 비반도체 제한 실행 + stale 서버 진단
- 목적: 반도체 외 은행 종목에서 전체 파이프라인과 신규 안전 원장 runtime 동작 확인.
- 실행:
  - 질의: JPM 향후 12개월 투자 매력도, NII/대손/자본규제/수수료/밸류에이션, Bull/Base/Bear 및 무효화 조건.
  - 제한: `max_iterations=1`, `max_sources=20`, 외부 호출 1회, 자동 재시도 없음.
  - 결과: status=done, 650.6초, 출처 223개, 실비용 $0.4112785, 오류 없음.
- 발견:
  - 8000 서버 프로세스가 `python -m uvicorn app.main:app --port 8000`로 `--reload` 없이 수정 전부터 실행 중.
  - `/openapi.json` 확인 결과 `safe_executive_summary`, `claim_ledger`, `calculation_ledger`, `scenario_analysis`, `search_attempts`가 모두 없는 stale 앱.
  - 따라서 유료 실행은 기존 파이프라인 runtime 정상만 증명하며 신규 기능 runtime 검증으로 간주하지 않음.
  - 프로세스 종료/새 백그라운드 서버 시작은 실행 정책에 막혀 강제 재기동하지 않음.
- 무비용 최신 코드 검증:
  - 새 Python 프로세스에서 `app.main:app` 직접 import 후 OpenAPI 확인 → 신규 필드 5개 전부 존재.
  - JPM fixture Claim Ledger → `verified`, executive eligible=True.
  - JPM Bull/Base/Bear fixture → valid, case 3개.
- 수정 파일: 제품 코드 수정 없음. `CODEX_WORKLOG.md` 기록만 추가.
- 남은 작업:
  - 사용자가 8000 서버를 최신 코드로 재시작한 뒤 외부 유료 호출은 추가 비용 승인 시 1회만 재검증.
  - stale 서버 방지를 위해 개발 실행 명령은 `--reload`, 운영 배포는 명시적 재시작 절차 필요.

### 추가(2026-07-24) - [FCF-05 완료] 최신 코드 JPM runtime 재검증
- 목적: stale 8000 서버 영향을 제거하고 최신 Claim/Calculation/Scenario/Search 원장이 실제 유료 파이프라인에서 직렬화·검증되는지 확인.
- 환경:
  - 최신 코드를 임시 8001 서버로 실행.
  - OpenAPI에서 `safe_executive_summary`, `claim_ledger`, `calculation_ledger`, `scenario_analysis`, `search_attempts` 5개 필드 존재 확인.
  - 검증 후 임시 서버 프로세스 종료. 기존 8000 서버는 건드리지 않음.
- 실행:
  - JPM 향후 12개월 투자 매력도, NII/대손/자본규제/수수료, Bull/Base/Bear/확률/무효화 조건.
  - `max_iterations=1`, `max_sources=12`, 외부 유료 호출 1회.
- 결과:
  - status=done, 오류 없음.
  - 569.8초, 비용 $0.3066185, 출처 122개.
  - claim 3개, executive eligible 0개.
  - `safe_executive_summary` 존재, 부적격 claim 혼입 0개. 통과 claim이 없어 안전 경고문을 표시한 보수적 정책 정상 작동.
  - scenario present=True, Bull/Base/Bear 3개, validation_status=valid, 오류 0.
  - search attempts 78개: success 18, no_results 48, not_searched 12.
  - counter evidence queries 2개.
  - 명시적 파생 계산은 추출되지 않아 calculation ledger 빈 상태로 관측. 자료 없는 계산 비생성 정책과 일치.
- 판정:
  - 반도체 외 은행 종목에서 신규 안전 계층 실제 runtime 통과.
  - 안전 요약 차단, 범용 시나리오 검증, 검색 관측, 반증 검색 정상.
- 남은 관찰:
  - claim 3개 모두 승격 불가였던 상세 상태 분포는 이번 요약 출력에 포함하지 않아 미확인. 다음 품질 감사 시 상태별 이유를 별도 저장/집계 권장.
  - PowerShell 콘솔의 UTF-8 표시가 깨졌으나 JSON 필드 존재와 판정 로직에는 영향 없음.

### 추가(2026-07-24) - [운영 반영] stale 서버 교체 및 로컬 smoke
- 목적: 최신 심층 리서치 안전 계층을 실제 8000/5173 로컬 앱에 반영.
- 조치:
  - 기존 수동 실행 stale 백엔드 PID 22776 종료.
  - `backend`에서 `python -m uvicorn app.main:app --reload --port 8000`으로 최신 서버 실행.
  - 프론트 Vite를 127.0.0.1:5173으로 실행.
  - 최초 실행 잔여 Vite 프로세스 1개를 식별해 종료하고 단일 listener만 유지.
- 검증:
  - 8000 listener 정상.
  - `/openapi.json`: safe_executive_summary/claim_ledger/calculation_ledger/scenario_analysis/search_attempts 모두 존재.
  - `GET /` → status=ok, FinVision API.
  - `GET http://127.0.0.1:5173` → HTTP 200.
  - 프론트 `/api/deep-research/nonexistent/status` → 백엔드 의도 응답 HTTP 404 전달, Vite proxy 정상.
- 수정 파일: 제품 코드 수정 없음. 작업 로그만 추가.
- 남은 필수 차단 문제: 현재 확인 없음.
- 선택 개선: provider 내부 예외 typed outcome 완전 통일, 산술 재계산 엔진, 번들 크기 최적화. 현 기능 사용을 막는 문제는 아님.
### 추가(2026-07-24 16:05:58 +09:00) - 범용 계산 재실행·동시 실행 격리·출처 메타데이터
- 목적:
  - Micron/DRAM 전용 규칙 없이 모든 기업·산업 계산을 코드가 직접 재실행해 LLM 계산 오류 차단.
  - 공유 mutable state 때문에 동시 리서치가 서로의 검색·추출·사용량 상태를 초기화할 수 있는 구조 차단.
  - 보고서 저장·재열람 후에도 출처를 재검증할 수 있도록 메타데이터 보존.
- 수정:
  - `backend/app/deep_research/agents/calculation_ledger.py`: Decimal 단위 정규화, AST 안전 수식 실행, 계산 불일치 차단.
  - `backend/app/deep_research/models.py`: 계산 재실행 값/차이와 출처 메타데이터 필드 추가.
  - `backend/app/deep_research/agents/synthesizer.py`: `formula_expression` 구조화 출력과 최종 출처 메타데이터 연결.
  - `backend/app/deep_research/agents/extractor.py`: 검색 메타데이터를 전문 추출 결과에 결합.
  - `backend/app/deep_research/storage/raw_sources.py`: 원문 증거와 출처 메타데이터 동시 보존.
  - `backend/app/deep_research/pipeline.py`: 동시 reset 오염 방지 run lock 추가.
  - `frontend/src/components/shared/StockResearchChat.jsx`: 재계산값·차이 및 출처 상세 메타데이터 표시.
  - `backend/tests/test_calculation_ledger.py`: 은행·SaaS·항공·에너지 공통 계산, 오류/위험 수식 차단 테스트.
  - `backend/tests/test_source_metadata.py`: 검색→추출→응답 및 RawSource 메타데이터 보존 테스트.
- 검증:
  - `PYTHONPATH=backend py -3.13 -m pytest backend/tests -q` → `354 passed`.
  - 변경 Python 파일 `py_compile` → 성공.
  - `frontend npm run build` → 성공, 2695 modules transformed.
- 환경:
  - 기존 `backend/venv`는 Linux 심볼릭 링크라 Windows 실행 불가.
  - Windows Python 3.13 검증용 pytest/FastAPI/aiosqlite/pypdfium2 설치. 저장소/API 키 변경 없음.
- 남은 리스크:
  - 실행식이 없거나 파싱 불가한 계산은 `needs_review`로 차단됨.
  - 통화 환산, 회계연도 매핑, 13주/14주 보정은 별도 기준값 기반 변환 엔진이 더 필요.
  - 일부 검색 provider가 내부 예외를 빈 목록으로 삼켜 `no_results`와 장애가 완전히 구분되지 않는 경로가 남음.
  - run lock은 정확성을 보장하지만 동시 실행을 직렬화함. 안전한 병렬화에는 run-local 에이전트 분리가 필요.
