"""§8 AI 분석층 — Story·시그널에 Gemini 정밀 영향도 스코어 부여.

§7이 산출한 Story(aggregated_impact=prescore 임시값)를 받아,
이벤트 내용 + 얕은/깊은 리서치 리포트를 Gemini에 제공하고
impact_score·direction·confidence를 재계산한다.

from ingest2.analyze.score import score_candidates, make_gemini_llm
"""
