# 크롤링 변경 이력

텔레그램 크롤러 수정/오류 대응 기록.
포맷: `## YYYY-MM-DD HH:MM` + 내용

---

## 2026-06-22 18:40 — 최초 구축

### 프로젝트 목적
텔레그램 채널/그룹 전용 크롤러.
사용자가 지정한 채널의 텍스트·이미지·영상 등 모든 데이터를 누락 없이 수집.

---

### 디렉토리 구조
```
finvision_crawling/
├── .env                  # API 키 (gitignore 처리)
├── .env.example          # 키 입력 예시
├── .gitignore
├── config.py             # 채널 목록, 수집 설정
├── setup.py              # 최초 로그인 (기기별 1회)
├── main.py               # 크롤러 실행 진입점
├── analyze.py            # Gemini 분석 실행 진입점
├── requirements.txt
├── CRAWL_LOG.md
├── README.md
├── crawler/
│   ├── __init__.py
│   ├── client.py         # Telethon 세션 관리
│   ├── collector.py      # 메시지/미디어 수집 본체
│   ├── parser.py         # 메시지 파싱 (텍스트/미디어/엔티티)
│   ├── storage.py        # SQLite 저장소
│   └── analyzer.py      # Gemini 멀티모달 분석기
├── data/
│   ├── telegram.db       # SQLite DB (gitignore)
│   └── media/            # 미디어 파일 (gitignore)
└── sessions/             # Telethon 세션 파일 (gitignore)
```

---

### 기술 스택
| 항목 | 선택 | 이유 |
|------|------|------|
| 크롤링 | Telethon (MTProto) | 공식 API, 봇 탐지 없음 |
| DB | SQLite (aiosqlite) | 로컬 독립, 별도 서버 불필요 |
| 분석 AI | Google Gemini | 멀티모달 (텍스트+이미지 동시 처리) |
| 스케줄 | APScheduler | 주기적 수집 보조 |

---

### 환경변수 (.env)
```
TELEGRAM_API_ID=       # my.telegram.org 발급
TELEGRAM_API_HASH=     # my.telegram.org 발급
TELEGRAM_PHONE=        # 국제형식 (예: +821012345678)
SESSION_NAME=          # 기기 구분용 이름 (기본: tg_session)
GOOGLE_API_KEY=        # Gemini API 키 (aistudio.google.com)
```

---

### DB 구조 (telegram.db)

#### 테이블
| 테이블 | 설명 |
|--------|------|
| `channels` | 수집 대상 채널 정보 (id, username, title, type) |
| `messages` | 전체 메시지 (id, channel_id, **post_id**, date, sender, text, raw_text, reply, forward, grouped_id, views, forwards, pinned) |
| `media` | 미디어 메타+경로 (message_id, channel_id, **post_id**, media_type, file_name, file_size, width, height, local_path, remote_file_id, url) |
| `entities` | URL/mention/hashtag/cashtag 추출 (message_id, channel_id, **post_id**, type, value) |
| `collection_state` | 채널별 마지막 수집 message_id |
| `analysis` | Gemini 분석 결과 (post_id, channel_id, model, prompt, result, image_count) |

#### 뷰
| 뷰 | 설명 |
|----|------|
| `posts` | 포스트 단위 통합 조회. 텍스트 + 미디어 목록이 post_id 기준으로 묶임 |

---

### post_id 개념 (핵심)
텔레그램 앨범(사진/영상 여러 개를 한 번에 보낸 것)은 내부적으로 여러 message_id를 가짐.
이를 하나의 포스트로 묶기 위해 `post_id` 도입:
- `grouped_id` 있음 → `post_id = grouped_id` (앨범 전체가 같은 post_id)
- `grouped_id` 없음 → `post_id = message_id` (단일 메시지)

분석 시 `posts` 뷰를 조회하면 텍스트 + 모든 미디어가 post_id 기준으로 자동 묶여서 나옴.

---

### 로컬 미디어 저장 구조
```
data/media/
  {channel_id}/
    {post_id}/          ← 같은 포스트의 모든 파일이 한 폴더
      photo_xxx.jpg
      IMG_0371.MP4
      IMG_0382.MP4
```
- 영상 썸네일은 저장하지 않음 (텍스트로 설명 대체 가능)
- `MEDIA_MAX_SIZE_MB` 초과 파일은 메타만 저장, 바이너리 스킵

---

### 수집 범위
| 종류 | 저장 |
|------|------|
| 텍스트 | DB messages.text |
| 이미지 (photo) | 로컬 파일 + DB 경로 |
| 영상 (video) | 로컬 파일 + DB 경로 |
| 문서 (document) | 로컬 파일 + DB 경로 |
| 스티커 | 로컬 파일 + DB 경로 |
| GIF | 로컬 파일 + DB 경로 |
| 음성/오디오 | 로컬 파일 + DB 경로 |
| 웹페이지 미리보기 | DB (url, title, description) |
| 위치 (geo) | DB (url: geo:lat,long) |
| 연락처 | DB |
| 투표 (poll) | DB |
| URL/mention/hashtag | DB entities 테이블 |

---

### 실행 순서
```bash
# 1. 설치
pip install -r requirements.txt

# 2. .env 생성 (.env.example 복사 후 값 입력)

# 3. 최초 로그인 (기기별 1회)
python setup.py

# 4. config.py 에서 CHANNELS 리스트에 채널 추가

# 5. 크롤링 실행
python main.py
# → 과거 전체 수집 후 실시간 대기

# 6. 분석 실행 (별도)
python analyze.py
```

---

### config.py 주요 설정
| 항목 | 기본값 | 설명 |
|------|--------|------|
| `CHANNELS` | `[]` | 수집할 채널 목록 (username, t.me/xxx, 초대링크 모두 가능) |
| `COLLECT_MEDIA` | `True` | 미디어 파일 다운로드 여부 |
| `COLLECT_HISTORY` | `True` | 최초 실행 시 과거 전체 수집 |
| `HISTORY_LIMIT` | `0` | 수집 개수 제한 (0=무제한) |
| `MEDIA_MAX_SIZE_MB` | `100` | 이 크기 초과 파일은 메타만 저장 |

---

### 멀티 기기 사용 방법
- GitHub에 올라가는 것: 코드만 (`.env`, `sessions/`, `data/` 는 gitignore)
- 각 기기에서:
  1. `.env` 직접 생성 (본인 API 키/전화번호 입력)
  2. `python setup.py` 로 본인 계정 세션 생성
  3. 각자 로컬 DB에 독립적으로 수집

---

### 분석 결과 저장 계획
- **현재**: `telegram.db` 내 `analysis` 테이블에 임시 저장
- **추후**: finvision 로컬 프로젝트 DB (`finvision.db`)에 직접 저장 (A안)
- Gemini 멀티모달 호출: 텍스트 + 이미지(photo만)를 하나의 API 요청으로 전송
- 영상은 분석 제외 (텍스트로 설명 대체)

---

### 대응 이력
| 날짜 | 문제 | 대응 |
|------|------|------|
| 2026-06-22 | Windows cp949 터미널 이모지 인코딩 오류 | print/log 이모지 제거, 파일 핸들러는 utf-8 유지 |
| 2026-06-22 | 앨범 메시지 미디어 중복 표시 | posts 뷰 서브쿼리 분리로 해결 |
| 2026-06-22 | google-generativeai deprecated | google-genai 패키지로 교체 |

---

<!-- 이후 변경 발생 시 아래에 추가 -->
