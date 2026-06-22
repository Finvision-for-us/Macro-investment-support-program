# Telegram Crawler

텔레그램 채널/그룹 전용 크롤러. 텍스트·미디어 누락 없이 수집.

---

## 시작 전 준비 — 반드시 직접 발급/입력해야 하는 정보

### 1. Telegram API 키 발급
> https://my.telegram.org 접속 → 본인 전화번호로 로그인 → **API development tools** 클릭
> → App title / Short name 아무거나 입력 → **Create application**

발급되는 값:
- `App api_id` : 숫자 (예: 31234567)
- `App api_hash` : 32자리 문자열 (예: abcdef1234567890abcdef1234567890)

### 2. Google Gemini API 키 발급 (분석 기능 사용 시)
> https://aistudio.google.com 접속 → **Get API key** → 새 키 생성

발급되는 값:
- API 키 문자열 (예: AIzaSy...)

---

## 설치

```bash
pip install -r requirements.txt
```

---

## .env 파일 생성

`.env.example` 을 복사해서 `.env` 로 이름 변경 후 아래 값 입력:

```
TELEGRAM_API_ID=여기에_api_id_숫자_입력
TELEGRAM_API_HASH=여기에_api_hash_입력
TELEGRAM_PHONE=여기에_전화번호_입력  # 예: +821012345678 (국제형식)
SESSION_NAME=tg_session              # 기기 구분용 이름, 아무거나 가능
GOOGLE_API_KEY=여기에_gemini_api_키_입력  # 분석 기능 사용 시
```

> `.env` 파일은 절대 GitHub에 올리지 말 것 (gitignore 처리되어 있음)

---

## 최초 로그인 (기기별 1회만 실행)

```bash
python setup.py
```

- 실행하면 텔레그램에서 인증코드 전송됨
- 코드 입력하면 `sessions/` 폴더에 세션 파일 생성
- 이후엔 `main.py` 만 실행하면 자동 로그인됨

---

## 수집할 채널 등록

`config.py` 열어서 `CHANNELS` 리스트에 추가:

```python
CHANNELS: list[str] = [
    "@channel_username",              # username 방식
    "https://t.me/channel_name",      # URL 방식
    "https://t.me/+초대링크해시",       # 비공개 채널 초대링크도 가능
]
```

채널 URL은 텔레그램에서 채널 들어가서 상단 이름 클릭 → 링크 복사하면 됨.

---

## 실행

```bash
python main.py
```

- 최초 실행: 채널 개설일부터 현재까지 전체 메시지 수집 → 이후 실시간 대기
- 재실행: 마지막 수집 이후 새 메시지만 추가 수집

---

## 수집 데이터 저장 위치

| 종류 | 저장 위치 |
|------|-----------|
| 텍스트·메타데이터 | `data/telegram.db` (SQLite) |
| 이미지·영상·파일 | `data/media/{채널ID}/{포스트ID}/` |

같은 포스트(채팅)에 보낸 텍스트 + 이미지 + 영상은 **같은 폴더에 묶여서 저장됨**.

---

## 주요 설정 (`config.py`)

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `CHANNELS` | `[]` | 수집할 채널 목록 |
| `COLLECT_MEDIA` | `True` | 이미지·영상 파일 다운로드 여부 |
| `COLLECT_HISTORY` | `True` | 과거 메시지 전체 수집 여부 |
| `HISTORY_LIMIT` | `0` | 수집 개수 제한 (0 = 무제한) |
| `MEDIA_MAX_SIZE_MB` | `100` | 이 크기 초과 파일은 메타만 저장, 파일 스킵 |

---

## DB 테이블 구조

| 테이블/뷰 | 설명 |
|-----------|------|
| `channels` | 등록된 채널 정보 |
| `messages` | 전체 메시지 원문 |
| `media` | 미디어 파일 메타 + 로컬 경로 |
| `entities` | URL / mention / hashtag / cashtag 추출 |
| `collection_state` | 채널별 마지막 수집 위치 |
| `analysis` | Gemini 분석 결과 |
| `posts` (뷰) | 포스트 단위 통합 조회 (텍스트 + 미디어 묶음) |

---

## 변경 이력

→ [CRAWL_LOG.md](CRAWL_LOG.md)
