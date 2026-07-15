"""수치 정합성 검사 (결정론적, 무할루시네이션).

Critic이 LLM 판단과 별개로 호출하는 **계산 기반** 점검 모듈.
LLM에게 산술을 시키지 않는다 — 산술은 코드가 한다. (LLM 산술 오류로
'허상 상충'을 만드는 사고를 방지: 예) 시중 딥리서치 AI가 열지 못한 숫자를
근거로 pro-rata 상충을 단정했던 사례.)

두 축을 본다:
1. 산술 정합(arithmetic): 같은 통화의 금액들이 명시된 비율(pro-rata) 또는
   세율(gross↔net)로 서로 환산했을 때 일치하는가.
2. 시간축·프레이밍(temporal/framing): 같은 크기의 금액이 서로 다른 수식어
   (gross vs net / 税前 vs 税后)나 서로 다른 기준일로 등장하는가.

원칙:
- 오직 실제 추출된 콘텐츠(ExtractedContent)만 대상으로 한다.
  '열지 못한 값'은 애초에 여기 들어오지 않으므로 상충 근거로 쓰이지 않는다.
- 새로운 사실을 만들지 않는다. 발견된 정합/불일치를 문장으로 기록하고,
  추가 검증이 필요하면 후속 쿼리 문자열만 제안한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── 수식어 토큰 (gross / net) ──
_GROSS_TOKENS = ("gross", "税前", "세전", "before tax", "毛额")
_NET_TOKENS = ("net of", "net proceeds", "net cash", "税后", "세후",
               "净额", "净收", "扣除", "after tax")

# ── 지분 비율 문맥 토큰 (pro-rata 후보 판별) ──
# '占'(占营收/占比)처럼 일반 비율에 붙는 토큰은 제외 — 지분(equity) 문맥만 인정.
_RATIO_CONTEXT = ("股权", "股份", "股本", "总股本", "equity interest",
                  "equity", "ownership", "stake", "持股", "持有", "权益",
                  "지분", "voting", "owned")

# ── 세율 문맥 토큰 ──
_TAX_CONTEXT = ("tax", "税", "세금", "withhold", "levy")

# ── '전체(100%/총액)' 문맥 토큰 — pro-rata의 '전체' 후보를 제한(오탐 방지 핵심) ──
_WHOLE_TOKENS = ("100%", "全部", "整体", "总对价", "总额", "总价", "整体估值",
                 "全部权益", "作价", "aggregate", "total consideration",
                 "entire equity", "whole")

# pro-rata: 최소 금액·허용오차·근접거리.
# 부분·전체·비율이 같은 출처의 근접 구간에 함께 있을 때만 성립시킨다.
# (코퍼스 전역 대조는 같은 비율로 우연히 맞는 무관한 쌍이 폭증 → 안전하게 제한.)
_MIN_PRORATA_VALUE = 1e6
_PRORATA_TOL = 0.015
_PRORATA_COLOCATION = 240
# 지분비율은 이 범위 밖이면 pro-rata에 쓰지 않는다. 근-100%(예: 99.26%)는
# 두 '거의 같은' 큰 숫자를 우연히 맞추는 노이즈, 근-0%도 무의미.
_PRORATA_RATIO_MIN = 0.01
_PRORATA_RATIO_MAX = 0.95
_TAX_TOL = 0.012          # 세율 gross↔net 환산 일치 허용오차
_TAX_COLOCATION = 240     # 세율 '정합'은 gross·net이 같은 문장(근접)에 함께 있을 때만

# 상대 오차 허용치
_SAME_MAGNITUDE_TOL = 0.005   # 같은 금액으로 볼 상대오차 (0.5%)
_RECONCILE_TOL = 0.03          # pro-rata/세율 환산 일치로 볼 상대오차 (3%)

# 프레이밍(gross↔net) 상충: 유의미 금액(≥100만) + 다수 출처(≥3)일 때만.
# (소액 line item이 gross/net 단어와 우연히 근접해 과다 발화하던 노이즈 억제.
#  실측상 딜 핵심 금액은 항상 6~10 출처에 등장하므로 ≥3은 안전.)
_FRAMING_MIN_VALUE = 1e6
_FRAMING_MIN_SOURCES = 3

# 통화 교차: '같은 문장 안 통화 등가액 쌍'으로 볼 최대 문자 거리
_COLOCATION_CHARS = 130
# 주요 통화쌍의 상식적 환율 밴드(넓게 — 단위/통화 오표기 같은 '큰 오류'만 잡는다).
# 값은 (통화A 1단위당 통화B 수)가 아니라 아래 함수에서 정의한 기준으로 해석.
_FX_BANDS = {
    frozenset(("RMB", "USD")): (5.0, 8.5),   # RMB per 1 USD
}
_FX_CROSS_SOURCE_TOL = 0.05    # 출처 간 함축환율 일치로 볼 상대오차 (5%)

# ── 크로스출처 pro-rata(엔티티 연결) ──
# 흩어진 출처의 '지분블록(part+지분%)'과 '전체(100%/총액)'를 환산해 정합시키되,
# 오탐 방지를 위해: 무모호(정확히 1개 매칭) + 큰 금액(≥1천만) + 아주 좁은 오차,
# 그리고 (공유 앵커) 또는 (거의 정확 ≤0.3%)일 때만 인정한다.
_XSRC_PRORATA_MATCH_TOL = 0.01   # 전체 후보 매칭 창(오차)
_XSRC_PRORATA_EXACT_TOL = 0.003  # 앵커 없이도 인정하는 '거의 정확' 임계
_XSRC_STAKE_COLOCATION = 120     # part와 지분%가 같은 지분블록으로 볼 최대 거리
_MIN_XSRC_PRORATA = 1e7          # 크로스출처는 딜 규모(≥1천만)만

# 앵커(고유 식별자) 추출: 6자리 코드 / 대문자 약어(티커) / 라틴 고유명.
# 일반 통화·법인·규제 약어는 제외(공유돼도 '같은 대상' 근거가 못 됨).
_ANCHOR_STOP = {
    # 통화·법인·규제 약어
    "RMB", "USD", "CNY", "HKD", "EUR", "JPY", "KRW", "GBP",
    "GAAP", "IFRS", "LLC", "LTD", "INC", "CO", "PLC", "LP",
    "SEC", "CSRC", "SZSE", "SSE", "HKEX", "NYSE", "NASDAQ", "PRC",
    "THE", "AND", "FOR", "VAT", "CEO", "CFO", "IPO", "ADR",
    # 공시/문서 boilerplate 흔한 대문자 단어 (가젯티어 전까지 오탐 차단)
    "COMPANY", "COMPANIES", "SHARE", "SHARES", "SHAREHOLDER", "SHAREHOLDERS",
    "STOCK", "EQUITY", "INTEREST", "AGREEMENT", "TRANSACTION", "CONSIDERATION",
    "PURSUANT", "ACCORDING", "WHEREAS", "PURCHASE", "SALE", "SELLER", "BUYER",
    "PARTY", "PARTIES", "BOARD", "DIRECTORS", "DIRECTOR", "MEETING", "NOTICE",
    "CLOSING", "TOTAL", "NET", "GROSS", "CASH", "VALUE", "PRICE", "AMOUNT",
    "REPORT", "FINANCIAL", "ANNUAL", "QUARTERLY", "SECURITIES", "EXCHANGE",
    "COMMISSION", "GROUP", "HOLDINGS", "LIMITED", "CORPORATION", "SECTION",
    "ARTICLE", "EXHIBIT", "FORM", "ITEM", "NOTE", "NOTES", "REGULATION",
    "RULE", "LAW", "UNDER", "UPON", "SUCH", "EACH", "ALL", "ANY", "OTHER",
    "SAME", "FIRST", "SECOND", "THIRD", "FOLLOWING", "ABOVE", "BELOW",
    "DURING", "AFTER", "BEFORE", "BETWEEN", "WITHIN", "INCLUDING", "PROVIDED",
    "HOWEVER", "THEREFORE", "ACCORDINGLY", "THIS", "THAT", "THESE", "THOSE",
    "ITS", "OUR", "THEIR", "WILL", "SHALL", "MAY", "MUST", "REVENUE",
    "PROFIT", "LOSS", "ASSETS", "LIABILITIES", "MILLION", "BILLION",
    "TARGET", "SUBSIDIARY", "PARENT", "ACQUISITION", "DISPOSAL", "DIVESTITURE",
}
_ANCHOR_RE = re.compile(r"\b\d{6}\b|\b[A-Z]{2,6}\b|\b[A-Z][a-z]{3,}\b")


@dataclass
class NumericMention:
    """콘텐츠에서 뽑아낸 하나의 수치 언급."""
    kind: str                      # 'money' | 'percent'
    value: float                   # money=기준단위 환산액, percent=0~1
    currency: Optional[str]        # 'USD' | 'RMB' | None(percent)
    raw: str                       # 원문 표기
    qualifiers: set[str] = field(default_factory=set)   # {'gross','net'}
    dates: set[str] = field(default_factory=set)        # {'2025','Q3','Dec 31'}
    ratio_context: bool = False    # 지분/비율 문맥 여부
    tax_context: bool = False      # 세금 문맥 여부
    whole_context: bool = False    # '100%/총액' 등 전체 금액 문맥 여부
    anchors: frozenset = frozenset()  # 주변 고유 식별자(코드/티커/고유명) — 엔티티 연결용
    source_id: int = -1
    domain: str = ""
    pos: int = -1                  # 원문 내 시작 오프셋(같은 문장 통화쌍 판별용)


@dataclass
class NumericConsistencyResult:
    consistent: list[str] = field(default_factory=list)   # 정합 확인 문장
    conflicts: list[str] = field(default_factory=list)     # 불일치/재확인 필요 문장
    followup_queries: list[str] = field(default_factory=list)


# ────────────────────────── 추출 ──────────────────────────

# 통화·단위 매핑
_UNIT_SCALE = {"亿": 1e8, "億": 1e8, "万": 1e4, "萬": 1e4}
_SCALE_WORD = {"billion": 1e9, "bn": 1e9, "b": 1e9,
               "million": 1e6, "mn": 1e6, "m": 1e6}
_CN_SUFFIX_CUR = {"美元": "USD", "美金": "USD", "港元": "HKD", "港币": "HKD",
                  "港幣": "HKD", "日元": "JPY", "日圆": "JPY", "欧元": "EUR",
                  "歐元": "EUR", "英镑": "GBP", "人民币": "RMB", "人民幣": "RMB", "元": "RMB"}
_PREFIX_CUR = {"人民币": "RMB", "人民幣": "RMB", "rmb": "RMB", "cny": "RMB", "¥": "RMB",
               "美元": "USD", "美金": "USD", "usd": "USD", "us$": "USD",
               "港元": "HKD", "港币": "HKD", "港幣": "HKD", "hk$": "HKD", "hkd": "HKD",
               "jpy": "JPY", "日元": "JPY", "eur": "EUR", "欧元": "EUR"}
_DOLLAR_REGION = {"hk": "HKD", "us": "USD", "nt": "TWD", "sg": "SGD",
                  "s": "SGD", "a": "AUD", "c": "CAD", "": "USD"}

# 한국어 통화어 → 코드 (한국어 단위 '억/만'은 中文 '亿/万'과 다른 글자)
_KO_CUR = {"달러": "USD", "원": "KRW", "위안": "RMB", "엔": "JPY", "유로": "EUR"}

# 순서 중요: 더 구체적인(단위·통화·스케일 포함) 패턴이 먼저 span을 claim한다.
_MONEY_PATTERNS = [
    # K0) 한국어 조(+억) 조합: "3조 5,000억 원" = 3e12 + 5000e8 → 3.5e12 KRW
    #     (조 미지원 시 '5,000억'만 잡혀 틀린 부분값이 검사에 유입됨 — 침묵보다 위험)
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*조(?:\s*([\d,]+(?:\.\d+)?)\s*억)?\s*"
                r"(달러|원|위안|엔|유로)"), "ko_jo"),
    # K1) 한국어 억(+만) 조합: "1억 3,500만 달러" = 1e8 + 3500e4 → 1.35e8 USD
    #     (FinVision 리포트는 한국어라 이 표기가 지배적 — 미지원 시 원장 대조가 무력화됨)
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*억(?:\s*([\d,]+(?:\.\d+)?)\s*만)?\s*"
                r"(달러|원|위안|엔|유로)"), "ko_eok"),
    # K2) 한국어 만 단독: "3,500만 달러" → 3.5e7
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*만\s*(달러|원|위안|엔|유로)"), "ko_man"),
    # A) CJK 숫자+단위(亿/万)(+통화접미): 1.35亿美元 / 27.95亿元 / 285,600万元 / '人民币9.6亿元'의 9.6亿元
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*([亿億万萬])\s*"
                r"(美元|美金|港元|港币|港幣|日元|日圆|欧元|歐元|英镑|人民币|人民幣|元)?"), "cjk_unit"),
    # B) 통화어 + 숫자 + 스케일어: USD 135 million / RMB 5 million / HK$1,050 million
    (re.compile(r"(人民币|人民幣|美元|美金|港元|港币|RMB|CNY|USD|HKD|JPY|EUR|US\$|HK\$|¥)\s?"
                r"([\d,]+(?:\.\d+)?)\s*(billion|bn|million|mn|m|b)\b", re.I), "cur_scaled"),
    # C) 지역$ + 숫자 + 스케일어: $135 million / HK$1,050 million
    (re.compile(r"(HK|US|NT|SG|S|A|C)?\$\s?([\d,]+(?:\.\d+)?)\s*"
                r"(billion|bn|million|mn|m|b)\b", re.I), "dollar_scaled"),
    # D) 통화어 + 숫자(절대): RMB 960,834,355 / 人民币960,834,355 — 뒤에 亿/万 오면 제외(A가 처리)
    (re.compile(r"(人民币|人民幣|美元|美金|港元|港币|RMB|CNY|USD|HKD|JPY|EUR|US\$|HK\$|¥)\s?"
                r"([\d,]+(?:\.\d+)?)(?!\s*[\d,.]*[亿億万萬])", re.I), "cur_abs"),
    # E) 지역$ + 숫자(절대): $135 / HK$1,050
    (re.compile(r"(HK|US|NT|SG|S|A|C)?\$\s?([\d,]+(?:\.\d+)?)", re.I), "dollar_abs"),
    # G) 숫자 + CJK 통화접미(단위 없음): 960,834,355元 / 135美元 / 50港元
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*"
                r"(美元|美金|港元|港币|港幣|日元|日圆|欧元|歐元|英镑|人民币|人民幣|元)"), "cn_suffix"),
]
_PERCENT_RE = re.compile(r"([\d]+(?:\.\d+)?)\s*%")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_QUARTER_RE = re.compile(r"\b(Q[1-4])\b", re.I)
_MONTHDAY_RE = re.compile(
    r"((?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}|\d{1,2}月\d{1,2}日|"
    r"一季度|二季度|三季度|四季度)", re.I)


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _parse_money(kind: str, m) -> Optional[tuple[float, str]]:
    """매치에서 (금액값, 통화코드)를 계산. 실패 시 None."""
    if kind == "ko_jo":
        jo = _to_float(m.group(1))
        if jo is None:
            return None
        eok = _to_float(m.group(2)) if m.group(2) else 0.0
        return jo * 1e12 + (eok or 0.0) * 1e8, _KO_CUR.get(m.group(3), "USD")
    if kind == "ko_eok":
        eok = _to_float(m.group(1))
        if eok is None:
            return None
        man = _to_float(m.group(2)) if m.group(2) else 0.0
        return eok * 1e8 + (man or 0.0) * 1e4, _KO_CUR.get(m.group(3), "USD")
    if kind == "ko_man":
        man = _to_float(m.group(1))
        if man is None:
            return None
        return man * 1e4, _KO_CUR.get(m.group(2), "USD")
    if kind == "cjk_unit":
        num = _to_float(m.group(1))
        if num is None:
            return None
        scale = _UNIT_SCALE.get(m.group(2), 1.0)
        cur = _CN_SUFFIX_CUR.get(m.group(3) or "", "RMB")
        return num * scale, cur
    if kind in ("cur_scaled", "cur_abs"):
        num = _to_float(m.group(2))
        if num is None:
            return None
        cur = _PREFIX_CUR.get((m.group(1) or "").lower(), "USD")
        if kind == "cur_scaled":
            num *= _SCALE_WORD.get((m.group(3) or "").lower(), 1.0)
        return num, cur
    if kind in ("dollar_scaled", "dollar_abs"):
        num = _to_float(m.group(2))
        if num is None:
            return None
        cur = _DOLLAR_REGION.get((m.group(1) or "").lower(), "USD")
        if kind == "dollar_scaled":
            num *= _SCALE_WORD.get((m.group(3) or "").lower(), 1.0)
        return num, cur
    if kind == "cn_suffix":
        num = _to_float(m.group(1))
        if num is None:
            return None
        return num, _CN_SUFFIX_CUR.get(m.group(2), "RMB")
    return None


def _window(text: str, start: int, end: int, radius: int = 90) -> str:
    # 한 문장 안의 통화 등가액(예: "RMB 960,834,355 ... 약 $135 million")이
    # 같은 gross/net 수식어를 공유하도록 창을 문장 수준으로 넓게 잡는다.
    return text[max(0, start - radius): end + radius]


def _collect_tags(win: str) -> tuple[set[str], set[str], bool, bool, bool]:
    low = win.lower()
    quals: set[str] = set()
    if any(t.lower() in low for t in _GROSS_TOKENS):
        quals.add("gross")
    if any(t.lower() in low for t in _NET_TOKENS):
        quals.add("net")
    dates: set[str] = set()
    dates.update(_YEAR_RE.findall(win))
    dates.update(m.upper() for m in _QUARTER_RE.findall(win))
    dates.update(_MONTHDAY_RE.findall(win))
    ratio = any(t.lower() in low for t in _RATIO_CONTEXT)
    tax = any(t.lower() in low for t in _TAX_CONTEXT)
    whole = any(t.lower() in low for t in _WHOLE_TOKENS)
    return quals, dates, ratio, tax, whole


def _extract_anchors(win: str) -> frozenset:
    """창(window)에서 고유 식별자(6자리 코드/티커/라틴 고유명)만 추출."""
    out = set()
    for m in _ANCHOR_RE.finditer(win):
        t = m.group(0)
        if t.upper() in _ANCHOR_STOP:
            continue
        out.add(t)
    return frozenset(out)


def extract_mentions(text: str, source_id: int = -1, domain: str = "") -> list[NumericMention]:
    """한 콘텐츠 텍스트에서 금액·퍼센트 언급을 추출."""
    mentions: list[NumericMention] = []
    if not text:
        return mentions

    seen_spans: list[tuple[int, int]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in seen_spans)

    for pat, kind in _MONEY_PATTERNS:
        for m in pat.finditer(text):
            if _overlaps(m.start(), m.end()):
                continue
            parsed = _parse_money(kind, m)
            if parsed is None:
                continue
            val, cur = parsed
            raw_disp = m.group(0).strip().rstrip(",.")
            win = _window(text, m.start(), m.end())
            quals, dates, ratio, tax, whole = _collect_tags(win)
            mentions.append(NumericMention(
                kind="money", value=val, currency=cur, raw=raw_disp,
                qualifiers=quals, dates=dates, ratio_context=ratio, tax_context=tax,
                whole_context=whole, anchors=_extract_anchors(win),
                source_id=source_id, domain=domain, pos=m.start(),
            ))
            seen_spans.append((m.start(), m.end()))

    for m in _PERCENT_RE.finditer(text):
        num = _to_float(m.group(1))
        if num is None or num > 100:
            continue
        win = _window(text, m.start(), m.end())
        quals, dates, ratio, tax, whole = _collect_tags(win)
        mentions.append(NumericMention(
            kind="percent", value=num / 100.0, currency=None, raw=m.group(0).strip(),
            qualifiers=quals, dates=dates, ratio_context=ratio, tax_context=tax,
            whole_context=whole, anchors=_extract_anchors(win),
            source_id=source_id, domain=domain, pos=m.start(),
        ))
    return mentions


# ────────────────────── 검사 1: 시간축/프레이밍 ──────────────────────

def find_framing_conflicts(mentions: list[NumericMention]) -> tuple[list[str], list[str]]:
    """같은 크기 금액이 gross/net 등 상충 수식어로 등장하는지 검사."""
    conflicts: list[str] = []
    queries: list[str] = []
    monies = [m for m in mentions if m.kind == "money"]

    used = [False] * len(monies)
    for i, a in enumerate(monies):
        if used[i]:
            continue
        group = [a]
        for j in range(i + 1, len(monies)):
            b = monies[j]
            if used[j] or b.currency != a.currency:
                continue
            if a.value > 0 and abs(a.value - b.value) / a.value <= _SAME_MAGNITUDE_TOL:
                group.append(b)
                used[j] = True
        used[i] = True
        if len(group) < 2:
            continue
        quals: set[str] = set()
        for g in group:
            quals |= g.qualifiers
        srcs = sorted({g.source_id for g in group if g.source_id >= 0})
        if ("gross" in quals and "net" in quals
                and a.value >= _FRAMING_MIN_VALUE
                and len(srcs) >= _FRAMING_MIN_SOURCES):
            conflicts.append(
                f"[프레이밍 상충] 동일 금액 {a.raw}({a.currency})이 "
                f"gross·net 양쪽 수식어로 출처 {len(srcs)}곳에 등장 — "
                f"세전/세후 기준을 원문에서 확정 필요."
            )
            queries.append(
                f"{a.raw} gross vs net proceeds tax withholding 세전 세후 확인"
            )
    return conflicts, queries


# ────────────────────── 검사 2: 산술 정합 ──────────────────────

def find_arithmetic_inconsistencies(
    mentions: list[NumericMention],
) -> tuple[list[str], list[str], list[str]]:
    """pro-rata(부분=전체×비율) 및 세율(net=gross×(1-세율)) 정합 검사."""
    consistent: list[str] = []
    conflicts: list[str] = []
    queries: list[str] = []

    monies = [m for m in mentions if m.kind == "money"]
    percents = [m for m in mentions if m.kind == "percent"]

    # ── pro-rata: 부분금액 ≈ 전체금액 × 비율 ──
    # 오탐 방지: '전체'는 반드시 100%/총액 문맥을 가져야 하고, 양쪽 모두 유의미한
    # 금액(≥100만)이어야 하며, 허용오차는 좁게(1.5%). 이렇게 하지 않으면 코퍼스에
    # 숫자가 많을 때 같은 비율로 우연히 맞는 무관한 쌍이 폭증한다.
    # [설계 의도] pro-rata는 '정합(consistent)'만 보고하고 '상충'은 발화하지 않는다.
    # 서로 다른 값이 비율로 안 맞는다고 '모순'으로 단정하면(무관한 쌍일 수 있으므로)
    # '미검증 값으로 상충 단정 금지' 원칙에 어긋난다 → 일치 확인 전용.
    _seen_prorata: set[tuple[int, int]] = set()
    ratio_pcts = [p for p in percents
                  if p.ratio_context and _PRORATA_RATIO_MIN <= p.value <= _PRORATA_RATIO_MAX and p.pos >= 0]
    for p in ratio_pcts:
        # 비율(%)과 같은 출처에서 근접(≤240자)한 금액만 후보로 삼는다.
        near = [m for m in monies
                if m.source_id == p.source_id and m.pos >= 0
                and m.value >= _MIN_PRORATA_VALUE
                and abs(m.pos - p.pos) <= _PRORATA_COLOCATION]
        for cur in {m.currency for m in near}:
            cur_monies = [m for m in near if m.currency == cur]
            wholes = [m for m in cur_monies if m.whole_context]
            for part in cur_monies:
                implied_whole = part.value / p.value
                match = None
                for whole in wholes:
                    if whole is part:
                        continue
                    # 대칭(양방향) 검증: 정방향(part/ratio≈whole)과 역방향
                    # (part≈whole×ratio) 오차가 모두 허용치 내일 때만 정합.
                    # 분모 기준이 달라 경계에서 어긋나는 '전체↔부분 착각' 케이스를
                    # 걸러낸다 — 진짜 정합(오차≈0)은 영향 없음.
                    fwd = abs(whole.value - implied_whole) / implied_whole
                    implied_part = whole.value * p.value
                    bwd = abs(part.value - implied_part) / implied_part if implied_part else 1.0
                    if fwd <= _PRORATA_TOL and bwd <= _PRORATA_TOL:
                        match = whole
                        break
                if match:
                    # 값 쌍으로 dedup (같은 금액의 영/중 표기 중복 방지)
                    key = (round(part.value), round(match.value))
                    if key not in _seen_prorata:
                        _seen_prorata.add(key)
                        rel = abs(match.value - implied_whole) / implied_whole
                        consistent.append(
                            f"[pro-rata 정합] {part.raw} ≈ {match.raw} × {p.raw} "
                            f"({cur}, 오차 {rel*100:.2f}%) — 부분/전체 일치."
                        )

    # ── 세율(gross↔net): net ≈ gross × (1 - 세율) ──
    # 오탐 방지: 타당한 원천징수율(5~35%)만, 유의미 금액(≥100만)만, 같은 통화만,
    # 좁은 오차(1.2%). 상충은 'gross와 사실상 같은 금액을 net으로도 부르는데
    # 세율만큼 낮지 않은' 경우에만 판정(같은 금액을 gross/net으로 혼용하는 상황).
    tax_pcts = [p for p in percents if p.tax_context and 0.05 <= p.value <= 0.35]
    gross_monies = [m for m in monies
                    if "gross" in m.qualifiers and m.value >= _MIN_PRORATA_VALUE]
    net_monies = [m for m in monies
                  if "net" in m.qualifiers and m.value >= _MIN_PRORATA_VALUE]
    for tp in tax_pcts:
        for g in gross_monies:
            implied_net = g.value * (1 - tp.value)
            same_cur_nets = [n for n in net_monies if n.currency == g.currency]
            if not same_cur_nets:
                continue
            # 정합: gross와 '같은 거래로 함께 서술된' net(같은 출처·근접)만 인정.
            # (교차출처의 서로 다른 총액이 세율로 우연히 맞는 오탐 방지.)
            colocated_nets = [
                n for n in same_cur_nets
                if n is not g and n.source_id == g.source_id
                and n.pos >= 0 and g.pos >= 0
                and abs(n.pos - g.pos) <= _TAX_COLOCATION
            ]
            matched = next(
                (n for n in colocated_nets
                 if abs(n.value - implied_net) / implied_net <= _TAX_TOL),
                None,
            )
            if matched:
                consistent.append(
                    f"[세율 정합] net {matched.raw} ≈ gross {g.raw} × (1-{tp.raw}) "
                    f"({g.currency}) — 세전/세후 환산 일치."
                )
            else:
                # 상충은 교차출처 허용: 같은 금액(≠본인)을 gross/net으로 부르는데
                # 세율만큼 안 낮은 경우(=같은 수치를 gross/net으로 혼용) 재확인.
                same_mag = [n for n in same_cur_nets
                            if n is not g and abs(n.value - g.value) / g.value <= 0.02]
                if same_mag:
                    conflicts.append(
                        f"[세율 환산 재확인] gross {g.raw} × (1-{tp.raw}) ≈ "
                        f"{implied_net:,.0f} {g.currency}이나 net으로도 같은 금액이 "
                        f"제시됨 — 같은 금액을 gross/net으로 혼용하는지 원문 확인 필요."
                    )
                    queries.append(f"{g.raw} net proceeds after {tp.raw} tax 세후 순액 확인")
    consistent = list(dict.fromkeys(consistent))
    conflicts = list(dict.fromkeys(conflicts))
    queries = list(dict.fromkeys(queries))
    return consistent, conflicts, queries


# ────────────────────── 검사 3: 통화 교차(환율) ──────────────────────

def find_cross_currency_inconsistencies(
    mentions: list[NumericMention],
) -> tuple[list[str], list[str], list[str]]:
    """같은 문장에 등장한 통화 등가액 쌍(예: RMB 960,834,355 ≈ $135M)의
    함축 환율이 상식 범위인지, 여러 출처 간 일관되는지 검사.

    큰 오류(단위 亿/万 혼동, 통화 오표기)를 잡는 게 목적 — 밴드는 넓게 잡는다.
    """
    consistent: list[str] = []
    conflicts: list[str] = []
    queries: list[str] = []

    monies = [m for m in mentions if m.kind == "money" and m.pos >= 0 and m.value > 0]
    by_src: dict[int, list[NumericMention]] = {}
    for m in monies:
        by_src.setdefault(m.source_id, []).append(m)

    rmb_usd_rates: list[float] = []
    for _sid, ms in by_src.items():
        ms = sorted(ms, key=lambda x: x.pos)
        for i in range(len(ms)):
            a = ms[i]
            for j in range(i + 1, len(ms)):
                b = ms[j]
                if b.pos - a.pos > _COLOCATION_CHARS:
                    break  # pos 정렬됨 → 더 뒤는 볼 필요 없음
                if a.currency == b.currency:
                    continue
                pair = frozenset((a.currency, b.currency))
                if pair == frozenset(("RMB", "USD")):
                    m_by = {a.currency: a, b.currency: b}
                    rmb, usd = m_by["RMB"].value, m_by["USD"].value
                    # 두 금액 모두 유의미(≥100만)해야 '같은 값의 이종통화 표기' 후보.
                    # 작은 숫자가 큰 금액과 우연히 근접한 경우(함축FX≈0)를 배제.
                    if usd <= 0 or rmb < _MIN_PRORATA_VALUE or usd < _MIN_PRORATA_VALUE:
                        continue
                    rate = rmb / usd  # RMB per USD
                    band = _FX_BANDS[pair]
                    # 정상범위의 ~10배 밖이면 '같은 값'이 아니라 무관한 쌍 → 침묵.
                    if not (band[0] / 10 <= rate <= band[1] * 10):
                        continue
                    if band[0] <= rate <= band[1]:
                        rmb_usd_rates.append(rate)
                        consistent.append(
                            f"[환율 정합] {m_by['RMB'].raw} ≈ {m_by['USD'].raw} "
                            f"(함축 FX ≈ {rate:.2f} RMB/USD)."
                        )
                    else:
                        conflicts.append(
                            f"[환율 이상] {m_by['RMB'].raw} ↔ {m_by['USD'].raw} "
                            f"함축 FX ≈ {rate:.2f} RMB/USD (정상범위 "
                            f"{band[0]}~{band[1]} 밖) — 단위(亿/万)·통화 표기 확인 필요."
                        )
                        queries.append(
                            f"{m_by['RMB'].raw} {m_by['USD'].raw} RMB USD 환율 단위 확인"
                        )

    # 출처 간 함축환율 일관성 (같은 환산이 여러 출처에서 크게 어긋나면 상충)
    if len(rmb_usd_rates) >= 2:
        srt = sorted(rmb_usd_rates)
        median = srt[len(srt) // 2]
        for r in rmb_usd_rates:
            if median > 0 and abs(r - median) / median > _FX_CROSS_SOURCE_TOL:
                conflicts.append(
                    f"[환율 상충] 함축 FX {r:.2f} RMB/USD가 출처간 중앙값 "
                    f"{median:.2f}에서 {int(_FX_CROSS_SOURCE_TOL*100)}% 넘게 이탈 "
                    f"— 통화 환산 재확인."
                )

    return (
        list(dict.fromkeys(consistent)),
        list(dict.fromkeys(conflicts)),
        list(dict.fromkeys(queries)),
    )


# ────────────── 검사 4: 크로스출처 pro-rata (엔티티 연결) ──────────────

def find_cross_source_prorata(mentions: list[NumericMention]) -> tuple[list[str], list[str]]:
    """흩어진 출처의 '지분블록(part+지분%)'과 '전체(100%/총액)'를 안전하게 정합.

    오탐 방지: 무모호(정확히 1개 매칭) + 큰 금액(≥1천만) + 좁은 매칭오차,
    그리고 (공유 앵커) 또는 (거의 정확 ≤0.3%)일 때만 인정.
    """
    consistent: list[str] = []
    queries: list[str] = []

    monies = [m for m in mentions if m.kind == "money"]
    percents = [m for m in mentions if m.kind == "percent"]

    # 지분블록: 지분%와 같은 출처·근접(≤120자)에 있는 유의미 금액을 part로.
    stake_blocks: list[tuple[NumericMention, NumericMention, frozenset]] = []
    for p in percents:
        if not (p.ratio_context and _PRORATA_RATIO_MIN <= p.value <= _PRORATA_RATIO_MAX and p.pos >= 0):
            continue
        for part in monies:
            if (part.source_id == p.source_id and part.pos >= 0
                    and part.value >= _MIN_XSRC_PRORATA
                    and abs(part.pos - p.pos) <= _XSRC_STAKE_COLOCATION):
                stake_blocks.append((part, p, part.anchors | p.anchors))

    wholes = [m for m in monies if m.whole_context and m.value >= _MIN_XSRC_PRORATA]

    seen: set[tuple[int, int]] = set()
    for part, p, blk_anchors in stake_blocks:
        implied = part.value / p.value
        # 다른 출처의 전체후보 중, 매칭창 안에 드는 것들
        cands = [
            w for w in wholes
            if w.currency == part.currency and w.source_id != part.source_id
            and abs(w.value - implied) / implied <= _XSRC_PRORATA_MATCH_TOL
        ]
        # 같은 값이 여러 출처에 인용된 것은 '모호'가 아니라 '더 잘 검증된' 것.
        # 값으로 dedup해 '서로 다른 값이 2개 이상'일 때만 모호로 보고 침묵.
        distinct: dict[int, NumericMention] = {}
        for c in cands:
            distinct.setdefault(round(c.value), c)
        if len(distinct) != 1:
            continue
        w = next(iter(distinct.values()))
        rel = abs(w.value - implied) / implied
        # 대칭(역방향) 검증 — co-located pro-rata와 동일한 방어
        implied_part = w.value * p.value
        rel_bwd = abs(part.value - implied_part) / implied_part if implied_part else 1.0
        if rel_bwd > _XSRC_PRORATA_MATCH_TOL:
            continue
        shared = blk_anchors & w.anchors
        # 라운드 비율(10%·40% 등 정수)은 라운드 금액과 우연히 정확히 맞는다.
        # 앵커가 없으면 '정밀(소수부 있는) 비율'만 exact-path로 인정(예: 34.3769%).
        pct = p.value * 100
        precise = abs(pct - round(pct)) > 0.01
        # 엔티티 연결: 공유 앵커가 있거나, (앵커 없이) 정밀 비율이 거의 정확히 맞을 때만.
        if not (shared or (rel <= _XSRC_PRORATA_EXACT_TOL and precise)):
            continue
        key = (round(part.value), round(w.value))
        if key in seen:
            continue
        seen.add(key)
        link = (f"공유 앵커 {sorted(shared)[0]}" if shared
                else "교차출처·거의 정확 일치(동일 대상 추정)")
        consistent.append(
            f"[pro-rata 정합·교차출처] {part.raw} ≈ {w.raw} × {p.raw} "
            f"({part.currency}, 오차 {rel*100:.2f}%, {link}) — 부분/전체 일치, 원문 확인 권장."
        )
        queries.append(f"{part.raw} {w.raw} {p.raw} 지분 전체 거래가 정합 확인")

    return list(dict.fromkeys(consistent)), list(dict.fromkeys(queries))


# ────────────────────────── 상위 진입점 ──────────────────────────

def _content_signature(text: str) -> str:
    """본문 정규화 서명(소문자·영숫자만, 앞 500자). verbatim 신디케이트 판별용."""
    return re.sub(r"[^0-9a-z一-鿿]", "", (text or "").lower())[:500]


def _dedup_contents(contents: list) -> list:
    """동일 기사가 여러 도메인에 전재된(verbatim) 중복 출처를 제거(첫 것 유지).

    프레이밍 '출처 N곳'·다출처 카운트가 신디케이트 복제로 부풀지 않게 한다.
    서명이 충분히 길고(≥60자) 동일한 경우만 중복 처리 → 서로 다른 기사는 안전.
    """
    seen: set[str] = set()
    out = []
    for c in contents:
        sig = _content_signature(getattr(c, "content", "") or "")
        if len(sig) >= 60:
            if sig in seen:
                continue
            seen.add(sig)
        out.append(c)
    return out


def analyze(contents) -> NumericConsistencyResult:
    """ExtractedContent 리스트를 받아 수치 정합성 결과를 반환.

    contents: .content / .domain 속성을 갖는 객체 리스트(ExtractedContent).
    """
    contents = _dedup_contents(list(contents))
    all_mentions: list[NumericMention] = []
    for i, c in enumerate(contents):
        text = getattr(c, "content", "") or ""
        domain = getattr(c, "domain", "") or ""
        all_mentions.extend(extract_mentions(text, source_id=i, domain=domain))

    result = NumericConsistencyResult()
    if not all_mentions:
        return result

    f_conf, f_q = find_framing_conflicts(all_mentions)
    a_cons, a_conf, a_q = find_arithmetic_inconsistencies(all_mentions)
    x_cons, x_conf, x_q = find_cross_currency_inconsistencies(all_mentions)
    xsrc_cons, xsrc_q = find_cross_source_prorata(all_mentions)

    result.consistent = a_cons + x_cons + xsrc_cons
    result.conflicts = f_conf + a_conf + x_conf
    result.followup_queries = list(dict.fromkeys(f_q + a_q + x_q + xsrc_q))
    return result
