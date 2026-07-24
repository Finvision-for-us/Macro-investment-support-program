"""
한국어/영어 종목명 사전 + 퍼지 검색

- 한국어 이름으로 검색 가능 (아마존 → AMZN)
- 오타 허용 (amazin, anmazon, 아마쥰 → AMZN)
- 대소문자 무시
- 부분 일치 지원
"""

import difflib
import json
import logging
import re
import time
from pathlib import Path

try:
    import requests
except ImportError:  # 오프라인/테스트 환경
    requests = None

logger = logging.getLogger(__name__)

# ── 한국어 초성 (Korean initial consonants) ─────────────────────────
CHOSUNG = [
    'ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ',
    'ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ',
]
_JAMO_CONSONANTS = set(CHOSUNG)

def get_chosung(text: str) -> str:
    """한국어 텍스트에서 초성만 추출 (비한글 문자는 그대로 유지)"""
    result = ''
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:  # 완성형 한글 음절
            cho_idx = (code - 0xAC00) // 588
            result += CHOSUNG[cho_idx]
        else:
            result += ch
    return result

def _is_all_chosung(text: str) -> bool:
    """텍스트가 모두 한국어 초성 자음인지 확인"""
    return len(text) > 0 and all(ch in _JAMO_CONSONANTS for ch in text)

# ── 주요 미국 주식 사전 ──────────────────────────────────────────
# (ticker, 영문명, 한국어명들, 한국어 별명들)
STOCK_DB = [
    # 빅테크
    ("AAPL",  "Apple Inc.",                    ["애플"]),
    ("MSFT",  "Microsoft Corporation",         ["마이크로소프트", "마소", "MS"]),
    ("GOOGL", "Alphabet Inc.",                 ["구글", "알파벳", "Google"]),
    ("GOOG",  "Alphabet Inc. Class C",         ["구글C"]),
    ("AMZN",  "Amazon.com Inc.",               ["아마존", "Amazon"]),
    ("META",  "Meta Platforms Inc.",            ["메타", "페이스북", "Facebook"]),
    ("NVDA",  "NVIDIA Corporation",            ["엔비디아", "엔비디아", "엔브이디에이"]),
    ("TSLA",  "Tesla Inc.",                    ["테슬라"]),
    ("AVGO",  "Broadcom Inc.",                 ["브로드컴"]),
    ("TSM",   "Taiwan Semiconductor",          ["TSMC", "대만반도체", "티에스엠씨"]),

    # 반도체
    ("AMD",   "Advanced Micro Devices",        ["AMD", "에이엠디"]),
    ("INTC",  "Intel Corporation",             ["인텔"]),
    ("QCOM",  "Qualcomm Inc.",                 ["퀄컴"]),
    ("MU",    "Micron Technology",             ["마이크론"]),
    ("ARM",   "Arm Holdings",                  ["ARM", "암홀딩스"]),
    ("ASML",  "ASML Holding NV",              ["ASML", "에이에스엠엘"]),
    ("MRVL",  "Marvell Technology",            ["마벨"]),
    ("LRCX",  "Lam Research",                  ["램리서치"]),
    ("AMAT",  "Applied Materials",             ["어플라이드머티리얼즈", "어플라이드"]),
    ("KLAC",  "KLA Corporation",               ["KLA"]),
    ("ON",    "ON Semiconductor",              ["온세미컨덕터", "온세미"]),
    ("TXN",   "Texas Instruments",             ["텍사스인스트루먼트", "TI"]),

    # 소프트웨어 / 클라우드
    ("CRM",   "Salesforce Inc.",               ["세일즈포스"]),
    ("ORCL",  "Oracle Corporation",            ["오라클"]),
    ("ADBE",  "Adobe Inc.",                    ["어도비"]),
    ("NOW",   "ServiceNow Inc.",               ["서비스나우"]),
    ("SHOP",  "Shopify Inc.",                  ["쇼피파이"]),
    ("SNOW",  "Snowflake Inc.",                ["스노우플레이크"]),
    ("PLTR",  "Palantir Technologies",         ["팔란티어"]),
    ("NET",   "Cloudflare Inc.",               ["클라우드플레어"]),
    ("PANW",  "Palo Alto Networks",            ["팔로알토"]),
    ("CRWD",  "CrowdStrike Holdings",          ["크라우드스트라이크"]),
    ("DDOG",  "Datadog Inc.",                  ["데이터독"]),
    ("ZS",    "Zscaler Inc.",                  ["지스케일러"]),
    ("MDB",   "MongoDB Inc.",                  ["몽고DB"]),
    ("UBER",  "Uber Technologies",             ["우버"]),
    ("ABNB",  "Airbnb Inc.",                   ["에어비앤비"]),
    ("SQ",    "Block Inc.",                    ["블록", "스퀘어"]),
    ("COIN",  "Coinbase Global",               ["코인베이스"]),

    # AI / 로봇
    ("AI",    "C3.ai Inc.",                    ["C3AI"]),
    ("PATH",  "UiPath Inc.",                   ["유아이패스"]),
    ("IONQ",  "IonQ Inc.",                     ["아이온큐", "이온큐"]),
    ("RGTI",  "Rigetti Computing",             ["리게티"]),
    ("SMCI",  "Super Micro Computer",          ["슈퍼마이크로", "SMCI"]),

    # 전기차 / 에너지
    ("RIVN",  "Rivian Automotive",             ["리비안"]),
    ("LCID",  "Lucid Group",                   ["루시드"]),
    ("NIO",   "NIO Inc.",                      ["니오"]),
    ("XPEV",  "XPeng Inc.",                    ["샤오펑"]),
    ("LI",    "Li Auto Inc.",                  ["리오토"]),
    ("ENPH",  "Enphase Energy",                ["엔페이즈"]),
    ("FSLR",  "First Solar",                   ["퍼스트솔라"]),
    ("PLUG",  "Plug Power",                    ["플러그파워"]),

    # 금융
    ("JPM",   "JPMorgan Chase",                ["JP모건", "제이피모건"]),
    ("BAC",   "Bank of America",               ["뱅크오브아메리카", "BOA"]),
    ("GS",    "Goldman Sachs",                 ["골드만삭스"]),
    ("MS",    "Morgan Stanley",                ["모건스탠리"]),
    ("WFC",   "Wells Fargo",                   ["웰스파고"]),
    ("C",     "Citigroup Inc.",                ["시티그룹", "씨티"]),
    ("V",     "Visa Inc.",                     ["비자"]),
    ("MA",    "Mastercard Inc.",               ["마스터카드"]),
    ("PYPL",  "PayPal Holdings",               ["페이팔"]),
    ("AXP",   "American Express",              ["아메리칸익스프레스", "아멕스"]),
    ("BRK-B", "Berkshire Hathaway",            ["버크셔해서웨이", "버크셔"]),
    ("BLK",   "BlackRock Inc.",                ["블랙록"]),
    ("SCHW",  "Charles Schwab",                ["찰스슈왑"]),

    # 헬스케어 / 제약
    ("JNJ",   "Johnson & Johnson",             ["존슨앤존슨", "J&J"]),
    ("UNH",   "UnitedHealth Group",            ["유나이티드헬스"]),
    ("PFE",   "Pfizer Inc.",                   ["화이자"]),
    ("MRNA",  "Moderna Inc.",                  ["모더나"]),
    ("ABBV",  "AbbVie Inc.",                   ["애브비"]),
    ("LLY",   "Eli Lilly",                     ["일라이릴리", "릴리"]),
    ("NVO",   "Novo Nordisk",                  ["노보노디스크"]),
    ("TMO",   "Thermo Fisher Scientific",      ["써모피셔"]),
    ("ABT",   "Abbott Laboratories",           ["애보트"]),
    ("BMY",   "Bristol-Myers Squibb",          ["브리스톨마이어스"]),
    ("AMGN",  "Amgen Inc.",                    ["암젠"]),
    ("GILD",  "Gilead Sciences",               ["길리어드"]),
    ("ISRG",  "Intuitive Surgical",            ["인튜이티브서지컬", "다빈치"]),

    # 소비재 / 유통
    ("WMT",   "Walmart Inc.",                  ["월마트"]),
    ("COST",  "Costco Wholesale",              ["코스트코"]),
    ("HD",    "Home Depot",                    ["홈디포"]),
    ("TGT",   "Target Corporation",            ["타겟"]),
    ("NKE",   "Nike Inc.",                     ["나이키"]),
    ("SBUX",  "Starbucks Corporation",         ["스타벅스"]),
    ("MCD",   "McDonald's Corporation",        ["맥도날드"]),
    ("KO",    "Coca-Cola Company",             ["코카콜라"]),
    ("PEP",   "PepsiCo Inc.",                  ["펩시", "펩시코"]),
    ("PG",    "Procter & Gamble",              ["P&G", "피앤지"]),
    ("CL",    "Colgate-Palmolive",             ["콜게이트"]),

    # 통신 / 미디어
    ("DIS",   "Walt Disney Company",           ["디즈니", "월트디즈니"]),
    ("NFLX",  "Netflix Inc.",                  ["넷플릭스"]),
    ("CMCSA", "Comcast Corporation",           ["컴캐스트"]),
    ("T",     "AT&T Inc.",                     ["AT&T", "에이티앤티"]),
    ("VZ",    "Verizon Communications",        ["버라이즌"]),
    ("TMUS",  "T-Mobile US",                   ["티모바일"]),
    ("SPOT",  "Spotify Technology",            ["스포티파이"]),
    ("RBLX",  "Roblox Corporation",            ["로블록스"]),
    ("SNAP",  "Snap Inc.",                     ["스냅", "스냅챗"]),
    ("PINS",  "Pinterest Inc.",                ["핀터레스트"]),
    ("ROKU",  "Roku Inc.",                     ["로쿠"]),

    # 산업 / 방위
    ("BA",    "Boeing Company",                ["보잉"]),
    ("LMT",   "Lockheed Martin",               ["록히드마틴"]),
    ("RTX",   "RTX Corporation",               ["레이시온", "RTX"]),
    ("NOC",   "Northrop Grumman",              ["노스럽그루먼"]),
    ("GE",    "General Electric",              ["제너럴일렉트릭", "GE"]),
    ("CAT",   "Caterpillar Inc.",              ["캐터필러"]),
    ("DE",    "Deere & Company",               ["디어", "존디어"]),
    ("HON",   "Honeywell International",       ["허니웰"]),
    ("UPS",   "United Parcel Service",         ["UPS"]),
    ("FDX",   "FedEx Corporation",             ["페덱스"]),

    # 에너지
    ("XOM",   "Exxon Mobil",                   ["엑슨모빌"]),
    ("CVX",   "Chevron Corporation",           ["셰브론", "쉐브론"]),
    ("COP",   "ConocoPhillips",                ["코노코필립스"]),

    # ETF
    ("SPY",   "SPDR S&P 500 ETF Trust",        ["SPY", "S&P500", "에스앤피"]),
    ("QQQ",   "Invesco QQQ Trust",             ["큐큐큐", "나스닥100"]),
    ("IWM",   "iShares Russell 2000",          ["러셀2000"]),
    ("DIA",   "SPDR Dow Jones",                ["다우존스"]),
    ("VTI",   "Vanguard Total Stock Market",   ["뱅가드"]),
    ("ARKK",  "ARK Innovation ETF",            ["아크", "ARKK"]),
    ("SOXL",  "Direxion Semiconductor Bull",   ["SOXL", "반도체3배"]),
    ("TQQQ",  "ProShares UltraPro QQQ",        ["TQQQ", "나스닥3배"]),
    ("SQQQ",  "ProShares UltraPro Short QQQ",  ["SQQQ", "나스닥인버스3배"]),
    ("TLT",   "iShares 20+ Year Treasury",     ["장기국채", "TLT"]),
    ("GLD",   "SPDR Gold Shares",              ["금ETF", "GLD"]),
    ("SLV",   "iShares Silver Trust",          ["은ETF"]),
    ("USO",   "United States Oil Fund",        ["원유ETF"]),
    ("XLE",   "Energy Select Sector SPDR",     ["에너지ETF"]),
    ("XLF",   "Financial Select Sector SPDR",  ["금융ETF"]),
    ("XLK",   "Technology Select Sector SPDR", ["기술ETF"]),
    ("XLV",   "Health Care Select Sector SPDR",["헬스케어ETF"]),
    ("SOXX",  "iShares Semiconductor ETF",     ["반도체ETF"]),
    ("VOO",   "Vanguard S&P 500 ETF",          ["VOO"]),
    ("VGT",   "Vanguard Information Technology",["뱅가드IT"]),
    ("SCHD",  "Schwab US Dividend Equity ETF", ["SCHD", "배당ETF"]),
]

# ── 검색 인덱스 구축 ─────────────────────────────────────────────
# query_key(소문자) → [(ticker, name, source_label)]
_INDEX = {}  # 정확/부분 매칭용
_ALL_KEYS = []  # 퍼지 매칭용
_CHOSUNG_INDEX = {}  # 초성 검색용 (chosung_string → [entries])

def _normalize(s: str) -> str:
    """소문자 + 공백/특수문자 제거"""
    return re.sub(r'[^a-z0-9가-힣]', '', s.lower())

def _build_index():
    global _INDEX, _ALL_KEYS, _CHOSUNG_INDEX
    for ticker, name, kr_names in STOCK_DB:
        entry = {"ticker": ticker, "name": name}

        # 티커 자체
        key = _normalize(ticker)
        _INDEX.setdefault(key, []).append(entry)

        # 영문명
        key = _normalize(name)
        _INDEX.setdefault(key, []).append(entry)

        # 영문명 단어들 (3글자 이상만 - 짧은 단어는 노이즈 유발)
        for word in name.split():
            wkey = _normalize(word)
            if len(wkey) >= 3:
                _INDEX.setdefault(wkey, []).append(entry)

        # 한국어 이름들
        for kr in kr_names:
            key = _normalize(kr)
            _INDEX.setdefault(key, []).append(entry)

        # 초성 인덱스 추가
        for kr in kr_names:
            chosung = get_chosung(kr)
            if chosung:  # 빈 문자열이 아닌 경우에만
                _CHOSUNG_INDEX.setdefault(chosung, []).append(entry)

    _ALL_KEYS = list(_INDEX.keys())

_build_index()


# ── 검색 함수 ────────────────────────────────────────────────────

def _search_chosung(query: str, max_results: int = 8) -> list:
    """초성 검색: 초성 문자열로 종목 검색"""
    seen = set()
    results = []

    def _add(entries):
        for e in entries:
            if e["ticker"] not in seen:
                seen.add(e["ticker"])
                results.append(e)

    # 정확 초성 일치
    if query in _CHOSUNG_INDEX:
        _add(_CHOSUNG_INDEX[query])

    # 초성 prefix 일치
    if len(results) < max_results:
        for cho_key, entries in _CHOSUNG_INDEX.items():
            if cho_key.startswith(query) and cho_key != query:
                _add(entries)
            if len(results) >= max_results:
                break

    return results[:max_results]


# ── 통합 티어 (낮을수록 강한 매치) ──
# 0 정확 티커 | 1 정확 이름 | 2 접두 | 3 초성 | 4 부분문자열 | 5 퍼지(오타)
# 로마자 브리지 결과는 +0.5 가산해 같은 티어 내에서 뒤로.

def _search_local_scored(query: str, max_results: int = 8) -> list:
    """큐레이트 사전 검색을 (티어, entry) 쌍으로 반환. search_local의 코어."""
    raw_q = query.strip()

    # 0단계: 초성 검색 (입력이 모두 한글 자음인 경우)
    if raw_q and _is_all_chosung(raw_q):
        return [(3, e) for e in _search_chosung(raw_q, max_results)]

    q = _normalize(query)
    if not q:
        return []

    seen_tickers = set()
    results = []

    def _add(entries, tier):
        for e in entries:
            if e["ticker"] not in seen_tickers:
                seen_tickers.add(e["ticker"])
                results.append((tier, e))

    # 1단계: 정확 일치
    if q in _INDEX:
        _add(_INDEX[q], 1)

    # 2단계: 시작 부분 일치 (prefix)
    if len(results) < max_results:
        for key in _ALL_KEYS:
            if key.startswith(q) and key != q:
                _add(_INDEX[key], 2)
            if len(results) >= max_results:
                break

    # 3단계: 부분 문자열 포함 (substring)
    if len(results) < max_results:
        for key in _ALL_KEYS:
            if q in key and not key.startswith(q):
                _add(_INDEX[key], 4)
            if len(results) >= max_results:
                break

    # 4단계: 퍼지 매칭 (오타 허용)
    if len(results) < max_results and len(q) >= 3:
        cutoff = 0.55 if len(q) >= 5 else 0.65
        close_matches = difflib.get_close_matches(q, _ALL_KEYS, n=6, cutoff=cutoff)
        for match in close_matches:
            _add(_INDEX[match], 5)
            if len(results) >= max_results:
                break

    return results[:max_results]


def search_local(query: str, max_results: int = 8) -> list:
    """로컬 큐레이트 사전 검색 (초성/오타/한국어명 지원). entry 리스트 반환."""
    return [e for _, e in _search_local_scored(query, max_results)]


# ── 한글 → 로마자 (Revised Romanization, 결정론) ────────────────────
# 한국식 음차를 영문 회사명에 근사시키는 브리지. 완벽하지 않음(애플→aepeul 등
# 실패 케이스 존재) — 큐레이트 사전 밑에 깔리는 '최선노력' 보조 경로로만 사용.
_ROM_CHO = ['g', 'kk', 'n', 'd', 'tt', 'r', 'm', 'b', 'pp', 's', 'ss',
            '', 'j', 'jj', 'ch', 'k', 't', 'p', 'h']
_ROM_JUNG = ['a', 'ae', 'ya', 'yae', 'eo', 'e', 'yeo', 'ye', 'o', 'wa', 'wae',
             'oe', 'yo', 'u', 'wo', 'we', 'wi', 'yu', 'eu', 'ui', 'i']
_ROM_JONG = ['', 'g', 'kk', 'ks', 'n', 'nj', 'nh', 'd', 'l', 'lg', 'lm', 'lb',
             'ls', 'lt', 'lp', 'lh', 'm', 'b', 'ps', 's', 'ss', 'ng', 'j',
             'ch', 'k', 't', 'p', 'h']


def _has_hangul(text: str) -> bool:
    return any(0xAC00 <= ord(ch) <= 0xD7A3 for ch in text)


def romanize_hangul(text: str) -> str:
    """완성형 한글 음절을 로마자로 변환(비한글은 그대로). 예: 인디 → indi."""
    out = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            s = code - 0xAC00
            out.append(_ROM_CHO[s // 588] + _ROM_JUNG[(s % 588) // 28]
                       + _ROM_JONG[s % 28])
        else:
            out.append(ch)
    return ''.join(out)


# ── SEC 전체 미국 상장 유니버스 (~10,400개) 검색 코퍼스 ──────────────
# sec_client는 같은 파일에서 ticker→CIK만 쓰지만, 여기선 회사명(title)도 써서
# '모든 미국기업'을 로컬에서 검색 가능하게 한다(Yahoo 한국어 미지원 우회 + 속도).
_SEC_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_HEADERS = {"User-Agent": "FinVision research admin@finvision.app"}
_SEC_CACHE = Path("data/company_tickers.json")
_SEC_TTL = 7 * 24 * 3600  # 유니버스 캐시 7일

# 회사명 접미어 — 퍼지/단어 매칭에서 제외(노이즈·비율 희석 방지)
_UNI_STOP = {
    "the", "inc", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "group", "holdings", "holding", "sa", "ag", "nv", "lp", "llc",
    "trust", "fund", "and", "of", "class", "common", "stock", "shares",
}

_universe_loaded = False
_uni_entries: list = []          # [{"ticker","name"}]
_uni_name_keys: list = []        # _normalize(name) 병렬 리스트 (부분문자열용)
_uni_by_ticker: dict = {}        # TICKER → entry
_uni_word_bucket: dict = {}      # 첫 글자 → [(유의미단어, entry)] (퍼지 버킷)


def _sig_words(name: str) -> set:
    """회사명에서 유의미 단어(정규화, 접미어 제외, 4자 이상) 집합."""
    words = set()
    for w in re.split(r"[^0-9A-Za-z가-힣]+", name):
        wn = _normalize(w)
        if len(wn) >= 4 and wn not in _UNI_STOP:
            words.add(wn)
    return words


def _load_sec_universe() -> None:
    """SEC company_tickers.json → 검색 인덱스 1회 구축(디스크 캐시 7일).

    실패 시 유니버스 빈 상태(큐레이트+Yahoo로 폴백 — 무회귀). LLM/네트워크
    실패가 파이프라인을 막지 않는다.
    """
    global _universe_loaded
    if _universe_loaded:
        return
    _universe_loaded = True  # 1회만 시도(실패해도 재시도 안 함 — 지연 방지)

    raw = None
    try:
        if _SEC_CACHE.exists() and (time.time() - _SEC_CACHE.stat().st_mtime) < _SEC_TTL:
            raw = json.loads(_SEC_CACHE.read_text(encoding="utf-8"))
    except Exception:
        raw = None
    if raw is None and requests is not None:
        try:
            r = requests.get(_SEC_URL, headers=_SEC_HEADERS, timeout=15)
            r.raise_for_status()
            raw = r.json()
            try:
                _SEC_CACHE.parent.mkdir(parents=True, exist_ok=True)
                _SEC_CACHE.write_text(json.dumps(raw), encoding="utf-8")
            except Exception:
                pass  # 캐시 저장 실패는 무해
        except Exception as e:
            logger.warning("[stock_dict] SEC 유니버스 로드 실패: %s", e)
            raw = None
    if not raw:
        return

    for entry in (raw.values() if isinstance(raw, dict) else raw):
        ticker = str(entry.get("ticker") or "").upper()
        name = str(entry.get("title") or "")
        if not ticker or not name or ticker in _uni_by_ticker:
            continue
        e = {"ticker": ticker, "name": name}
        nkey = _normalize(name)
        _uni_by_ticker[ticker] = e
        _uni_entries.append(e)
        _uni_name_keys.append(nkey)
        for w in _sig_words(name):
            _uni_word_bucket.setdefault(w[0], []).append((w, e))
    logger.info("[stock_dict] SEC 유니버스 %d개 로드", len(_uni_entries))


def _search_universe_scored(query: str, max_results: int = 8) -> list:
    """SEC 유니버스에서 (티어, entry) 반환. 정확티커/이름접두/부분/퍼지."""
    _load_sec_universe()
    if not _uni_entries:
        return []
    q = _normalize(query)
    qu = query.strip().upper()
    if not q:
        return []

    scored = []  # (tier, name_len, entry)
    for e, nkey in zip(_uni_entries, _uni_name_keys):
        t = e["ticker"]
        if t == qu:
            tier = 0
        elif nkey == q:
            tier = 1
        elif len(q) >= 2 and (t.lower().startswith(q) or nkey.startswith(q)):
            tier = 2
        elif len(q) >= 3 and q in nkey:
            tier = 4
        else:
            continue
        scored.append((tier, len(e["name"]), e))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = [(tier, e) for tier, _, e in scored[:max_results]]

    # 퍼지(오타): 강한 매치가 부족할 때만. 유의미 단어(접미어 제외) 대조 —
    # 'nvida'를 전체 'nvidiacorp'가 아니라 'nvidia'와 비교(비율 희석 방지).
    # 첫 글자 버킷으로 후보 축소(속도).
    strong = sum(1 for tier, _ in out if tier <= 2)
    if strong < max_results and len(q) >= 4:
        bucket = _uni_word_bucket.get(q[0], [])
        keys = list({w for w, _ in bucket})
        matches = set(difflib.get_close_matches(q, keys, n=8, cutoff=0.72))
        seen = {e["ticker"] for _, e in out}
        if matches:
            for w, e in bucket:
                if w in matches and e["ticker"] not in seen:
                    seen.add(e["ticker"])
                    out.append((5, e))
                    if len(out) >= max_results:
                        break
    return out[:max_results]


def search_suggest(query: str, max_results: int = 8) -> list:
    """큐레이트 사전 + SEC 유니버스 + 한글 로마자 브리지 통합 검색.

    두 소스를 매치 품질(티어)로 병합 — 유니버스 정확/접두 매치가 큐레이트
    퍼지 노이즈보다 위로 온다. 한글 입력은 로마자로 변환해 유니버스에도 질의
    (예: '인디' → 'indi' → INDI). entry 리스트 반환.
    """
    query = (query or "").strip()
    if not query:
        return []

    scored = []  # (tier, entry)
    scored.extend(_search_local_scored(query, max_results))
    scored.extend(_search_universe_scored(query, max_results))

    # 한글 로마자 브리지 → 유니버스 (티어 +0.5로 동급 내 후순위)
    if _has_hangul(query):
        rq = romanize_hangul(query)
        if rq != query and re.search(r'[a-z]', rq):
            scored.extend((tier + 0.5, e)
                          for tier, e in _search_universe_scored(rq, max_results))

    # 티커별 최상(최저 티어) 유지 후 티어순 정렬
    best: dict = {}
    for tier, e in scored:
        cur = best.get(e["ticker"])
        if cur is None or tier < cur[0]:
            best[e["ticker"]] = (tier, e)
    ranked = sorted(best.values(), key=lambda x: x[0])
    return [{"ticker": e["ticker"], "name": e["name"]} for _, e in ranked[:max_results]]
