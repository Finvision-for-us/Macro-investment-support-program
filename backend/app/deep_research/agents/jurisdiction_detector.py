"""관할 감지기 — 쿼리에서 대상 국가·시장을 파악."""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 거래소·기관 약어 (ALL-CAPS 티커 오인 방지)
_EXCHANGE_ABBREVIATIONS: frozenset[str] = frozenset([
    "SSE", "SZSE", "CSRC", "HKEX", "SEC", "KRX", "DART", "NYSE", "NASDAQ",
    "ESMA", "ECB", "FCA", "FSC", "BOK", "BOJ", "JPX", "FSA", "PBOC",
    "SAFE", "MOFCOM", "SAMR", "BIS", "IMF", "WTO", "OECD", "TSE", "OSE",
    "LSE", "BSE", "NSE", "ASX", "SGX", "MOEF", "FRED", "BEA", "BLS",
    "ADR", "ETF", "IPO", "AUM", "EPS", "GDP", "CPI", "PPI",
    "FOMC", "FED", "BOE", "RBA", "SNB", "M&A",
    "CEO", "CFO", "COO", "CTO", "RSU", "ESG",
    "US", "CN", "KR", "JP", "EU", "UK", "GB", "HK", "MX", "IN",
    "DE", "FR", "IT", "BR", "AU", "CA", "RU", "SA", "UAE", "SG", "TW",
])

# 한국어 조사가 붙은 ALL-CAPS 티커 (INDI의, NVDA가, AAPL은 ...)
_TICKER_KO_RE = re.compile(r'\b([A-Z]{2,5})[의가는은을를이와과도만]\b')
# 독립 ALL-CAPS 티커
_TICKER_RE = re.compile(r'\b([A-Z]{2,5})\b')

# 이벤트 타입 패턴
# ASCII는 \b 경계, 한국어/한자는 경계 없이 포함 (Korean \b가 \w로 처리되어 경계 불일치 방지)
_EVENT_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("asset_sale",
     r"\b(?:divest\w*|disposal|asset.{0,5}sale|stake.{0,5}sale|出售|股权转让|资产出售)\b"
     r"|매각|자산.{0,3}매각|지분.{0,3}매각"),
    ("acquisition",
     r"\b(?:acqui\w*|merger|takeover|收购|并购)\b"
     r"|인수|합병"),
    ("export_control",
     r"\b(?:export.{0,5}restrict\w*|export.{0,5}control|sanction\w*|出口管制|制裁)\b"
     r"|수출.{0,3}규제"),
    ("supply_chain",
     r"\b(?:supply.{0,5}chain|manufactur\w*|供应链|工厂)\b"
     r"|공장|공급망"),
    ("dual_listing",
     r"\b(?:adr|dual.{0,5}list\w*|h.{0,3}share|双重上市)\b"
     r"|이중.{0,3}상장"),
    ("regulatory",
     r"\b(?:regulat\w*|approv\w*|监管|批准)\b"
     r"|공시"),
]

# ── 국가 시그널 ──
_CN_SIGNALS: list[str] = [
    r"\b(sse|szse|hkex|hkexnews|csrc|shse|北交所)\b",
    r"\b(shanghai|shenzhen|hong ?kong|wuxi|guangzhou|hangzhou|chengdu|beijing|nanjing|tianjin|suzhou)\b",
    r"\b(a[- ]?share|h[- ]?share)\b",
    r"\b(중국|중화|홍콩)\b",
    r"\b차이나\b",
    r"\b(china|chinese|sino)\b",
    r"\b(alibaba|tencent|baidu|jd\.?com|pinduoduo|byd|huawei|xiaomi|wuxi ?apptec|catl)\b",
    r"\b(rmb|renminbi|yuan|cny)\b",
    r"\b(pboc|safe|mofcom|samr)\b",
    r"\b(vie structure|variable interest entity)\b",
    r"\b(우시|상하이|선전|베이징|광저우|청두|항저우)\b",
]

_KR_SIGNALS: list[str] = [
    r"\b(krx|kospi|kosdaq|dart|fsc|bok)\b",
    r"\b(한국|코스피|코스닥|한은)\b",
    r"\b(korea|korean|south korea)\b",
    r"\b(samsung|sk hynix|lg|hyundai|kakao|naver|krafton)\b",
    r"\b(won|krw|원화)\b",
]

_JP_SIGNALS: list[str] = [
    r"\b(jpx|tse|ose|fsa|boj|edinet)\b",
    r"\b(일본|도쿄|오사카)\b",
    r"\b(japan|japanese|tokyo|osaka)\b",
    r"\b(toyota|softbank|sony|nintendo|honda|mitsubishi|nomura)\b",
    r"\b(yen|jpy|엔화)\b",
]

_EU_SIGNALS: list[str] = [
    r"\b(esma|ecb|euronext|lse|fca|bafin)\b",
    r"\b(유럽|유로존|유로화)\b",
    r"\b(europe|european)\b",
    r"\b(euro|eur|pound|gbp)\b",
    r"\b(paris|frankfurt|amsterdam|london|dublin)\b",
    r"\b(lvmh|asml|sap|hsbc|barclays|bnp)\b",
]

_US_SIGNALS: list[str] = [
    r"\b(sec|edgar|nyse|nasdaq|fed|treasury|bls|bea|fred)\b",
    r"\b(미국|미연준|미재무부)\b",
    r"\b(united states|u\.s\.|us market|wall street)\b",
    r"\b(dollar|usd|s&p|dow jones)\b",
    r"\b(apple|microsoft|google|amazon|meta|tesla|nvidia|jpmorgan|blackrock)\b",
]

_IN_SIGNALS: list[str] = [
    r"\b(sebi|nse india|bse india|bombay stock)\b",
    r"\b(india|indian)\b",
    r"\b(인도|뭄바이)\b",
    r"\b(rupee|inr)\b",
    r"\b(infosys|wipro|tata|reliance|hdfc)\b",
]

_MX_SIGNALS: list[str] = [
    r"\b(bmv|bolsa mexicana|cnbv)\b",
    r"\b(mexico|mexican|monterrey|guadalajara)\b",
    r"\b(멕시코|메히코)\b",
    r"\b(peso|mxn)\b",
]

_SIGNAL_MAP: list[tuple[str, list[str]]] = [
    ("CN", _CN_SIGNALS),
    ("KR", _KR_SIGNALS),
    ("JP", _JP_SIGNALS),
    ("EU", _EU_SIGNALS),
    ("IN", _IN_SIGNALS),
    ("MX", _MX_SIGNALS),
    ("US", _US_SIGNALS),
]


@dataclass
class JurisdictionResult:
    primary: str
    secondary: list[str]
    is_cross_border: bool = False
    confidence: float = 0.0
    event_type: str = ""
    signals: dict[str, list[str]] = field(default_factory=dict)


def _detect_event_type(query: str) -> str:
    q_lower = query.lower()
    for event_type, pattern in _EVENT_TYPE_PATTERNS:
        if re.search(pattern, q_lower, re.IGNORECASE):
            return event_type
    return ""


def _detect_tickers(query: str) -> list[str]:
    """쿼리에서 ALL-CAPS 티커 감지. 거래소/기관 약어 제외."""
    found: list[str] = []
    # 한국어 조사 붙은 티커 우선
    for m in _TICKER_KO_RE.finditer(query):
        t = m.group(1)
        if t not in _EXCHANGE_ABBREVIATIONS:
            found.append(t)
    # 독립 ALL-CAPS
    for m in _TICKER_RE.finditer(query):
        t = m.group(1)
        if t not in _EXCHANGE_ABBREVIATIONS and t not in found:
            found.append(t)
    return found


class JurisdictionDetector:

    def detect(
        self,
        query: str,
        context: Optional[dict] = None,
    ) -> JurisdictionResult:
        q_lower = query.lower()
        context = context or {}
        scores: dict[str, int] = {}
        matched: dict[str, list[str]] = {}

        # context ticker → US 가중치 (최우선, +3)
        ctx_ticker = context.get("ticker", "")
        if ctx_ticker and ctx_ticker.upper() not in _EXCHANGE_ABBREVIATIONS:
            scores["US"] = scores.get("US", 0) + 3
            matched.setdefault("US", []).append(f"ctx_ticker:{ctx_ticker}")

        # query 내 ALL-CAPS 티커 감지 → US +2 per ticker
        tickers = _detect_tickers(query)
        if tickers:
            scores["US"] = scores.get("US", 0) + len(tickers) * 2
            matched.setdefault("US", []).extend(f"ticker:{t}" for t in tickers)

        # 국가 시그널 매칭
        for country, patterns in _SIGNAL_MAP:
            for pat in patterns:
                hits = re.findall(pat, q_lower, re.IGNORECASE)
                if hits:
                    scores[country] = scores.get(country, 0) + len(hits)
                    matched.setdefault(country, []).extend(
                        h if isinstance(h, str) else h[0] for h in hits
                    )

        event_type = _detect_event_type(query)

        if not scores:
            return JurisdictionResult(
                primary="US", secondary=[], is_cross_border=False,
                confidence=0.4, event_type=event_type, signals={},
            )

        sorted_countries = sorted(scores, key=lambda c: scores[c], reverse=True)
        primary = sorted_countries[0]
        secondary = [c for c in sorted_countries[1:] if scores.get(c, 0) >= 1]

        total = sum(scores.values())
        confidence = min(scores[primary] / max(total, 1), 1.0)

        # cross-border 판정
        # 1. secondary 점수가 primary의 30% 이상
        top_sec_score = scores.get(secondary[0], 0) if secondary else 0
        is_cross_border = top_sec_score >= scores[primary] * 0.3

        # 2. US ticker 특수 규칙: ticker 시그널이 있는데 primary가 비US → cross-border
        has_us_ticker = bool(ctx_ticker or tickers)
        has_non_us = any(c != "US" and scores.get(c, 0) >= 1 for c in scores)
        if has_us_ticker and has_non_us:
            is_cross_border = True
            if "US" not in secondary and primary != "US":
                secondary = ["US"] + secondary

        logger.debug(
            f"[jurisdiction] primary={primary}({scores.get(primary,0)}) "
            f"secondary={secondary[:2]} cross={is_cross_border} event={event_type}"
        )

        return JurisdictionResult(
            primary=primary,
            secondary=secondary[:3],
            is_cross_border=is_cross_border,
            confidence=confidence,
            event_type=event_type,
            signals=matched,
        )


jurisdiction_detector = JurisdictionDetector()
