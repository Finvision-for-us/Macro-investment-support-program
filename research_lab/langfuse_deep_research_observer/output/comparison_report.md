# Deep Research Comparison Report

채점은 전부 결정론적 '품질 비율' 지표다(카운트·자기신고 점수 미사용).
N/A = 로그에 해당 데이터가 없어 측정 불가 — 총점(normalized)에서 가중치 제외.

## Scores

| Engine | Total(norm) | Jurisdiction | Queries | Official Sources | Evidence | Search | Cross Check | Gaps | Answer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini | 79.75 | 15.0 | 15.0 | 13.33 | 12.0 | 6.25 | 6.67 | 10.0 | 1.5 |
| openai | 67.72 | 15.0 | 10.0 | 15.0 | 10.5 | 3.89 | 3.33 | 10.0 | 0.0 |
| finvision | 53.33 | 7.5 | 10.0 | 15.0 | 15.0 | 4.17 | 1.67 | 0.0 | 0.0 |

## Pairwise

### gemini vs openai

- Overall: **gemini** (normalized Δ 12.03)
- Citation domain Jaccard: 0.667
- Official domains only openai found: csrc.gov.cn
- Category verdicts: jurisdiction_detection→tie, query_generation→gemini, official_source_coverage→tie, evidence_quality→tie, search_behavior→gemini, cross_validation→gemini, gap_handling→tie, final_answer_structure→gemini

### gemini vs finvision

- Overall: **gemini** (normalized Δ 26.42)
- Citation domain Jaccard: 0.333
- Official domains only gemini found: hkexnews.hk
- Category verdicts: jurisdiction_detection→gemini, query_generation→gemini, official_source_coverage→tie, evidence_quality→finvision, search_behavior→gemini, cross_validation→gemini, gap_handling→gemini, final_answer_structure→gemini

### openai vs finvision

- Overall: **openai** (normalized Δ 14.39)
- Citation domain Jaccard: 0.5
- Official domains only openai found: csrc.gov.cn, hkexnews.hk
- Category verdicts: jurisdiction_detection→openai, query_generation→tie, official_source_coverage→tie, evidence_quality→finvision, search_behavior→tie, cross_validation→openai, gap_handling→openai, final_answer_structure→tie

## Trace Summary

### gemini

- Query: INDI의 Wuxi 매각이 어떤 의미이고 중국/홍콩 공시에서 확인할 내용이 있나?
- Detected jurisdictions: US, CN, HK
- Evidenced jurisdictions: CN, HK, US
- Generated queries: 6
- Official source queries: 7
- Sources found: 3
- Citations: 3
- Unverified gaps: 3

### openai

- Query: INDI Wuxi 매각의 의미와 공식 출처 검증
- Detected jurisdictions: US, CN, HK
- Evidenced jurisdictions: CN, HK, US
- Generated queries: 9
- Official source queries: 11
- Sources found: 2
- Citations: 2
- Unverified gaps: 1

### finvision

- Query: INDI Wuxi 매각 의미 분석
- Detected jurisdictions: US, CN
- Evidenced jurisdictions: US
- Generated queries: 6
- Official source queries: 5
- Sources found: 2
- Citations: 1
- Unverified gaps: 0

## FinVision Improvement Raw Material

### missing_official_source

- Description: External research checked csrc.gov.cn but FinVision did not.
- Suggested fix: Add csrc.gov.cn to official source discovery when the query context matches.
- Priority: high

### missing_official_source

- Description: External research checked hkexnews.hk but FinVision did not.
- Suggested fix: Add hkexnews.hk to official source discovery when the query context matches.
- Priority: high

### missing_jurisdiction

- Description: External research evidenced HK sources but FinVision did not.
- Suggested fix: Expand jurisdiction detector keywords and source registry coverage for HK.
- Priority: medium

### gap_handling

- Description: External research explicitly listed unverified gaps but FinVision did not.
- Suggested fix: Add a required uncertainty/gap section to FinVision synthesis output.
- Priority: medium

### official_query_generation

- Description: FinVision official-source query ratio 60% vs best external 89%.
- Suggested fix: Generate more site-specific queries for regulators, exchanges, and issuer IR pages.
- Priority: medium

