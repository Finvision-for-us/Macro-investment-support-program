# Deep Research Comparison Report

## Scores

| Engine | Total | Jurisdiction | Queries | Official Sources | Evidence | Search | Cross Check | Gaps | Answer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini | 89.48 | 15.0 | 15.0 | 18.0 | 12.5 | 7.83 | 6.67 | 10.0 | 4.48 |
| openai | 84.07 | 15.0 | 15.0 | 16.0 | 13.5 | 7.67 | 3.33 | 10.0 | 3.57 |
| finvision | 65.88 | 10.0 | 15.0 | 16.0 | 13.75 | 5.5 | 3.33 | 0.0 | 2.3 |

## Trace Summary

### gemini

- Query: INDI의 Wuxi 매각이 어떤 의미이고 중국/홍콩 공시에서 확인할 내용이 있나?
- Detected jurisdictions: US, CN, HK
- Generated queries: 6
- Official source queries: 7
- Sources found: 3
- Citations: 3
- Tool calls: 3
- Unverified gaps: 3

### openai

- Query: INDI Wuxi 매각의 의미와 공식 출처 검증
- Detected jurisdictions: US, CN, HK
- Generated queries: 9
- Official source queries: 11
- Sources found: 2
- Citations: 2
- Tool calls: 2
- Unverified gaps: 1

### finvision

- Query: INDI Wuxi 매각 의미 분석
- Detected jurisdictions: US, CN
- Generated queries: 6
- Official source queries: 5
- Sources found: 2
- Citations: 1
- Tool calls: 1
- Unverified gaps: 0

## FinVision Improvement Raw Material

### missing_official_source

- Description: External research checked www.hkexnews.hk but FinVision did not.
- Suggested fix: Add www.hkexnews.hk to official source discovery when the query context matches.
- Priority: high

### missing_jurisdiction

- Description: External research detected HK but FinVision did not.
- Suggested fix: Expand jurisdiction detector keywords and source registry coverage for HK.
- Priority: medium

### gap_handling

- Description: External research explicitly listed unverified gaps but FinVision did not.
- Suggested fix: Add a required uncertainty/gap section to FinVision synthesis output.
- Priority: medium

### official_query_generation

- Description: FinVision generated fewer official-source queries than the external research logs.
- Suggested fix: Generate more site-specific queries for regulators, exchanges, and issuer IR pages.
- Priority: medium
