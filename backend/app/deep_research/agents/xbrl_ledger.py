"""방어선 4c: SEC XBRL 원장 대조 — 보고서 수치를 공시 원장 값과 대조.

기존 검증(source_matcher)은 "리포트 숫자가 수집 텍스트에 축자 존재하는가"까지만
본다. 이 모듈은 한 단계 강한 검증을 더한다: **"리포트의 재무 수치가 SEC XBRL
공시 원장(companyfacts) 값과 일치하는가"**. LLM이 관여하지 않는 순수
조회+비교라 무할루시네이션 원칙에 부합한다.

원칙 (확인 전용):
- 일치를 '확인'만 한다. 원장에 없는 수치는 침묵 — 딜 대가·백로그처럼 재무제표
  밖의 정당한 수치가 많으므로 '원장에 없음'을 오류로 단정하면 그 자체가
  '미검증 근거 상충 단정'이 된다.
- **개념-주장 일치**: 값이 맞아도 주장 문맥과 XBRL 개념 범주가 맞아야 한다.
  (2026-07-20 INDI 감사 실측: 값만 보면 Non-GAAP 순손실 $15.1M↔InterestPaidNet,
  무맥락 $11.1M↔OtherAccruedLiabilities, 구조조정↔2024 상각 같은 허위 '일치'가
  6건 중 3건 — 가짜 신뢰를 부여한다.) 문맥에 범주 신호가 없거나 Non-GAAP/
  조정 수치(GAAP 원장에 없는 게 정상)면 침묵이 옳다.
- **기간-주장 일치**: 주장 문맥이 회계연도·분기를 명시하면 원장 항목의
  fy/fp와 일치해야 한다. (2026-07-22 외부 감사 지적: 개념이 맞아도 2024 Q2
  값을 2026 Q1 주장에 붙이면 오판. 문서 §5.1의 기간 요건.) 주장에 기간
  신호가 없으면 기간 제약을 걸지 않고 최신 항목 우선(종전 동작 유지).
- 네트워크/파싱 실패 시 빈 결과 + 경고 로그. 파이프라인은 절대 죽지 않는다.

데이터: data.sec.gov/api/xbrl/companyfacts/CIK##########.json (무료, 키 불필요)
- CIK 해석은 services.sec_client.get_cik(프로젝트 단일 CIK 소스) 재사용.
- 응답은 수 MB — 파일 캐시(TTL 1일) 후 값 정렬 인덱스로 bisect 근접 탐색.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_UA = {"User-Agent": "FinVision research admin@finvision.app"}
_CACHE_DIR = Path("data/xbrl_facts")
_CACHE_TTL = 24 * 3600          # companyfacts 파일 캐시 1일
_MIN_LEDGER_VALUE = 1_000_000   # $1M 미만 값은 우연 일치가 많아 대조 제외
_MATCH_TOL = 0.005              # 보고 수치(반올림) ↔ 원장 값 상대오차 0.5%
# 라운드 값(정확히 $1M 단위, 예: "$135 million")은 근접 우연이 구조적이다 —
# 라이브 실측: 5,148개 원장에서 $135M이 2023 영업손실(-135,423,000, 0.31%)과도,
# 0.1%로 조여도 2022 Q2 Liabilities(135,070,000, 0.05%)와도 겹쳤다.
# → 라운드 값은 '정수 정확 일치'만 인정(우연이어도 값 존재 자체는 참).
_ROUND_MATCH_TOL = 0.0
_MAX_STATEMENTS = 6             # cross_validation 노이즈 방지 상한


@dataclass
class LedgerFact:
    concept: str    # us-gaap 개념명 (예: CashAndCashEquivalentsAtCarryingValue)
    value: float    # 원장 값 (USD, 원 단위)
    end: str        # 기간 종료일 (YYYY-MM-DD)
    fy: str         # 회계연도 라벨 (예: 2026)
    fp: str         # 회계기간 라벨 (FY/Q1/Q2/Q3/Q4)
    form: str       # 출처 서식 (10-K/10-Q/8-K)
    start: str = ""  # 기간 시작일(duration 개념만; instant는 빈값) — 분기/YTD 구분용


class XbrlLedger:
    """티커 1개의 companyfacts를 값-정렬 인덱스로 보관, 근접 대조를 제공."""

    def __init__(self, ticker: str, facts: list[LedgerFact]):
        self.ticker = ticker
        # abs(value) 기준 정렬 — 손실/유출(음수)도 보고서엔 크기로 언급되므로
        self._facts = sorted(facts, key=lambda f: abs(f.value))
        self._keys = [abs(f.value) for f in self._facts]

    def __len__(self) -> int:
        return len(self._facts)

    def match(self, amount: float, tol: float = _MATCH_TOL) -> list[LedgerFact]:
        """amount(USD)와 상대오차 tol 이내인 원장 항목들 (최신 end 우선)."""
        if amount < _MIN_LEDGER_VALUE or not self._facts:
            return []
        lo_v, hi_v = amount * (1 - tol), amount * (1 + tol)
        lo = bisect.bisect_left(self._keys, lo_v)
        hi = bisect.bisect_right(self._keys, hi_v)
        hits = self._facts[lo:hi]
        # 같은 (concept, end, start) 중복 제거(10-K/10-Q 재보고), 최신 end 우선.
        # start를 키에 포함해 같은 종료일의 '단일 분기 vs 연초 누적'(값이 다름)이
        # 뭉개지지 않게 한다 — 값 매칭이 올바른 기간을 자연 선택.
        seen: set[tuple[str, str, str]] = set()
        uniq: list[LedgerFact] = []
        for f in sorted(hits, key=lambda f: f.end, reverse=True):
            k = (f.concept, f.end, f.start)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(f)
        return uniq


# ── companyfacts 취득 (파일 캐시 → 네트워크) ──

def _cache_path(cik: str) -> Path:
    return _CACHE_DIR / f"CIK{cik}.json"


async def _fetch_company_facts(cik: str) -> Optional[dict]:
    p = _cache_path(cik)
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) < _CACHE_TTL:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass  # 캐시 손상 → 네트워크로

    url = _FACTS_URL.format(cik=cik)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=_UA)
            if resp.status_code != 200:
                logger.warning(f"[xbrl] companyfacts HTTP {resp.status_code} (CIK{cik})")
                return None
            data = resp.json()
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass  # 캐시 저장 실패는 무해
        return data
    except Exception as e:
        logger.warning(f"[xbrl] companyfacts 조회 실패 (CIK{cik}): {e}")
        return None


def _build_facts(raw: dict) -> list[LedgerFact]:
    """companyfacts JSON → USD LedgerFact 리스트 (us-gaap USD 단위만)."""
    out: list[LedgerFact] = []
    gaap = (raw.get("facts") or {}).get("us-gaap") or {}
    for concept, body in gaap.items():
        units = (body.get("units") or {})
        for pt in units.get("USD", []):
            v = pt.get("val")
            if not isinstance(v, (int, float)) or abs(v) < _MIN_LEDGER_VALUE:
                continue
            out.append(LedgerFact(
                concept=concept,
                value=float(v),
                end=pt.get("end") or "",
                fy=str(pt.get("fy") or ""),
                fp=pt.get("fp") or "",
                form=pt.get("form") or "",
                start=pt.get("start") or "",
            ))
    return out


# ── 프로세스 내 원장 캐시 (티커당 1회 구축) ──
_ledger_cache: dict[str, Optional[XbrlLedger]] = {}


async def get_ledger(ticker: str) -> Optional[XbrlLedger]:
    """ticker → XbrlLedger. CIK 미해석/조회 실패 시 None (침묵)."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None
    if ticker in _ledger_cache:
        return _ledger_cache[ticker]

    # CIK: 프로젝트 단일 소스(services.sec_client) 재사용 — 동기 함수라 스레드로
    try:
        from app.services.sec_client import get_cik
        cik = await asyncio.to_thread(get_cik, ticker)
    except Exception as e:
        logger.warning(f"[xbrl] CIK 해석 실패 ({ticker}): {e}")
        cik = None
    if not cik:
        _ledger_cache[ticker] = None
        return None

    raw = await _fetch_company_facts(cik)
    if raw is None:
        _ledger_cache[ticker] = None
        return None

    facts = _build_facts(raw)
    ledger = XbrlLedger(ticker, facts) if facts else None
    _ledger_cache[ticker] = ledger
    if ledger:
        logger.info(f"[xbrl] {ticker} 원장 구축: {len(ledger)}개 USD 항목 (CIK{cik})")
    return ledger


# ── 개념-주장 범주 매칭 ──
# (주장 문맥 키워드, XBRL 개념명 부분문자열) 쌍 — 하나라도 겹쳐야 '일치' 인정.
# 키워드는 리포트 실문장 기준의 실용 목록(감사 오탐 3건 + 정당 매치 3건으로 보정).
_CONCEPT_CATEGORIES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("revenue", "sales", "매출"), ("Revenue", "Sales")),
    (("net loss", "net income", "순손실", "순이익", "당기순"),
     ("NetIncomeLoss", "ProfitLoss")),
    (("operating loss", "operating income", "영업손실", "영업이익"),
     ("OperatingIncomeLoss",)),
    (("gross profit", "gross margin", "매출총이익"), ("GrossProfit",)),
    (("cash", "현금"), ("Cash",)),
    (("convertible", "notes", "debt", "차입", "사채", "전환사채", "offering",
      "placement", "발행", "조달", "borrow"),
     ("Debt", "Notes", "Borrow", "Proceeds", "Placement", "Financ", "Repay")),
    (("r&d", "research and development", "연구개발"),
     ("ResearchAndDevelopment",)),
    (("operating expense", "sg&a", "selling, general", "판관비", "영업비용"),
     ("OperatingExpenses", "SellingGeneralAndAdministrative")),
    (("interest", "이자"), ("Interest",)),
    (("amortization", "depreciation", "상각", "감가상각"),
     ("Amortization", "Depreciation", "Depletion")),
    (("impairment", "손상차손", "손상"), ("Impairment",)),
    (("restructuring", "구조조정"), ("Restructuring",)),
    (("goodwill", "영업권"), ("Goodwill",)),
    (("intangible", "무형자산"), ("IntangibleAsset",)),
    (("acquisition", "acquire", "인수", "merger", "합병", "매각", "divest",
      "disposal", "consideration", "대가"),
     ("BusinessCombination", "Consideration", "Disposal", "Acquisition")),
    (("total assets", "총자산", "자산총계"), ("Assets",)),
    (("total liabilities", "총부채", "부채총계", "accrued", "미지급"),
     ("Liabilities",)),
    (("stockholders", "shareholders equity", "자본총계", "자기자본"),
     ("StockholdersEquity", "Equity")),
    (("stock-based compensation", "주식보상", "주식기준보상"),
     ("ShareBasedCompensation",)),
    (("inventory", "재고"), ("Inventor",)),
    (("capital expenditure", "capex", "설비투자"),
     ("PaymentsToAcquireProperty", "PropertyPlantAndEquipment")),
    (("tax", "법인세", "세금"), ("Tax",)),
]

# Non-GAAP/조정 수치는 GAAP 원장에 없는 게 정상 — 값 일치는 우연이므로 침묵
_NONGAAP_RE_STR = ("non-gaap", "non gaap", "adjusted", "조정 ", "조정된",
                   "비일반회계", "ebitda")

_CTX_WINDOW = 90  # 금액 표기 앞뒤 문맥 창(문자)


def _claim_context(text: str, raw: str, cursor: int) -> tuple[str, int]:
    """금액 표기(raw)의 주장 문맥 창과 다음 커서. 못 찾으면 전체 재탐색."""
    pos = text.find(raw, cursor)
    if pos < 0:
        pos = text.find(raw)
    if pos < 0:
        return "", cursor
    ctx = text[max(0, pos - _CTX_WINDOW):pos + len(raw) + _CTX_WINDOW]
    return ctx.lower(), pos + len(raw)


def _concept_allowed(ctx: str, concept: str) -> Optional[bool]:
    """주장 문맥 대비 개념 허용 판정.

    None = 문맥에 범주 신호 없음(판단 불가 → 침묵),
    True/False = 신호 있음 → 개념 범주 일치 여부.
    """
    found_any = False
    for claim_kws, concept_subs in _CONCEPT_CATEGORIES:
        if not any(kw in ctx for kw in claim_kws):
            continue
        found_any = True
        if any(sub in concept for sub in concept_subs):
            return True
    return False if found_any else None


# ── 기간-주장 매칭 (문서 §5.1: 개념이 맞아도 기간이 틀리면 오판) ──
# 4자리 회계연도(2000~2099). 앞뒤 숫자 배제로 금액 내부 숫자열 오탐 방지.
# (2000~2039만 잡으면 2040+ 오연도가 '연도 신호 없음'으로 새어 기간 무검사됨.)
_YEAR_IN_CTX = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
_QUARTER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Q1", re.compile(r"1\s*분기|Q\s*1|1Q\d{2}|first\s+quarter", re.IGNORECASE)),
    ("Q2", re.compile(r"2\s*분기|Q\s*2|2Q\d{2}|second\s+quarter", re.IGNORECASE)),
    ("Q3", re.compile(r"3\s*분기|Q\s*3|3Q\d{2}|third\s+quarter", re.IGNORECASE)),
    ("Q4", re.compile(r"4\s*분기|Q\s*4|4Q\d{2}|fourth\s+quarter", re.IGNORECASE)),
]


def _claim_period(ctx: str) -> tuple[set[str], set[str]]:
    """주장 문맥에서 회계연도 집합·분기 집합을 추출. (years, quarters)."""
    years = set(_YEAR_IN_CTX.findall(ctx))
    quarters = {q for q, pat in _QUARTER_PATTERNS if pat.search(ctx)}
    return years, quarters


def _period_ok(fact: LedgerFact, years: set[str], quarters: set[str]) -> bool:
    """원장 항목이 주장 기간과 양립하는가.

    연도: 주장 연도가 있으면 fact.fy(없으면 end 연도)가 그 집합에 있어야 함.
    분기: 주장 분기가 있으면 fact.fp가 분기 태그일 때 일치해야 하고, 연간(FY)
    태그면 분기 주장과 불일치로 기각. fp가 없으면(예: DEF 14A) 분기는 판단
    불가로 통과(연도로만 거른다 — 메타데이터 누락에 과잉 기각 방지).
    """
    if years:
        fy = fact.fy or ""
        end_year = fact.end[:4] if len(fact.end) >= 4 else ""
        if fy not in years and end_year not in years:
            return False
    if quarters:
        fp = (fact.fp or "").upper()
        if fp in ("Q1", "Q2", "Q3", "Q4"):
            if fp not in quarters:
                return False
        elif fp == "FY":
            return False  # 분기 주장에 연간 값 — 불일치
    return True


# ── 보고서 수치 대조 ──

def verify_amounts_against_ledger(text: str, ledger: XbrlLedger) -> list[str]:
    """텍스트의 USD 금액(≥$1M)을 원장과 대조해 '[원장 일치]' 문장을 생성.

    확인 전용 — 미매치 수치는 침묵(재무제표 밖 수치가 정상적으로 존재).
    값 일치 + **개념-주장 범주 일치**를 모두 요구한다. 문맥에 범주 신호가
    없거나 Non-GAAP/조정 수치면 침묵(허위 신뢰 부여 방지 — 감사 실측 규칙).
    통화 파싱은 numeric_consistency의 검증된 파서를 재사용한다.
    """
    if not text or ledger is None or len(ledger) == 0:
        return []
    from app.deep_research.agents.numeric_consistency import extract_mentions

    statements: list[str] = []
    seen_values: set[int] = set()
    cursor = 0
    for m in extract_mentions(text):
        if m.kind != "money" or m.currency != "USD":
            continue
        if m.value < _MIN_LEDGER_VALUE:
            continue
        ctx, cursor = _claim_context(text, m.raw, cursor)
        key = round(m.value)
        if key in seen_values:
            continue
        if any(kw in ctx for kw in _NONGAAP_RE_STR):
            continue
        # 라운드 값(정확히 $1M 단위)은 엄격 오차 — 우연 매치 억제
        tol = _ROUND_MATCH_TOL if m.value % 1_000_000 == 0 else _MATCH_TOL
        hits = ledger.match(m.value, tol=tol)
        # 개념-주장 범주 필터: 신호 없으면 전부 기각(침묵), 있으면 일치 개념만
        hits = [h for h in hits if _concept_allowed(ctx, h.concept)]
        # 기간-주장 필터: 문맥에 연도/분기가 있으면 원장 기간과 일치하는 것만
        years, quarters = _claim_period(ctx)
        if hits and (years or quarters):
            hits = [h for h in hits if _period_ok(h, years, quarters)]
        if not hits:
            continue
        seen_values.add(key)
        top = hits[0]
        period = f"{top.fy} {top.fp}".strip() or top.end  # DEF 14A 등 fy/fp 누락 폴백
        label = f"{top.concept} {top.value:,.0f} ({period}, {top.form})"
        extra = f" 외 {len(hits) - 1}건" if len(hits) > 1 else ""
        statements.append(
            f"[원장 일치] {m.raw} ≈ {label}{extra} — SEC XBRL 공시 원장과 일치."
        )
        if len(statements) >= _MAX_STATEMENTS:
            break
    return statements


async def verify_report_numbers(text: str, ticker: str) -> list[str]:
    """상위 진입점: ticker의 원장을 확보해 텍스트 수치를 대조. 실패 시 []."""
    try:
        ledger = await get_ledger(ticker)
        if ledger is None:
            return []
        return verify_amounts_against_ledger(text, ledger)
    except Exception as e:
        logger.warning(f"[xbrl] 원장 대조 실패(무시): {e}")
        return []
