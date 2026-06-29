# finvision UI (M4 미니)

`/today` 단일 페이지 — Top 10 스토리 + lifecycle 상태 (🟢/🟡/⚫) 배지.

## 데이터 소스
백엔드 `python -m src.cli lifecycle link <label>` 실행 시 갱신되는
`../data/stories_latest.json` 을 직접 읽는다. DB 없음.

## 개발
```bash
cd ui
npm install
npm run dev          # http://localhost:3000
```

## 빌드
```bash
npm run build && npm run start
```

## 타입 체크
```bash
npm run type-check
```

## 로드맵
- Day 8 ✅ 부트스트랩 (Next 14 + Tailwind 3 + TS)
- Day 9~10: `/today` Top 10 카드 + 상태 배지 — `lib/stories.ts` 가 JSON 읽음
- Day 11~12: narrative 펼침 / ticker 필터
- Day 13: 스타일링 final
