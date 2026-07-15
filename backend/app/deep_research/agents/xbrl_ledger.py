"""방어선 4c: SEC XBRL 원장 대조 — 보고서 수치를 공시 원장 값과 대조.

기존 검증(source_matcher)은 "리포트 숫자가 수집 텍스트에 축자 존재하는가"까지만
본다. 이 모듈은 한 단계 강한 검증을 더한다: **"리포트의 재무 수치가 SEC XBRL
공시 원장(companyfacts) 값과 일치하는가"**. LLM이 관여하지 않는 순수
조회+비교라 무할루시네이션 원칙에 부합한다.

원칙 (확인 전용):
- 일치를 '확인'만 한다. 원장에 없는 수치는 침묵 — 딜 대가·백로그처럼 재무제표
  밖의 정당한 수치가 많으므로 '원장에 없음'을 오류로 단정하면 그 자체가
  '미검증 근거 상충 단정'이 된다.
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
        # 같은 (concept, end) 중복 제거(10-K/10-Q 재보고), 최신 end 우선
        seen: set[tuple[str, str]] = set()
        uniq: list[LedgerFact] = []
        for f in sorted(hits, key=lambda f: f.end, reverse=True):
            k = (f.concept, f.end)
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


# ── 보고서 수치 대조 ──

def verify_amounts_against_ledger(text: str, ledger: XbrlLedger) -> list[str]:
    """텍스트의 USD 금액(≥$1M)을 원장과 대조해 '[원장 일치]' 문장을 생성.

    확인 전용 — 미매치 수치는 침묵(재무제표 밖 수치가 정상적으로 존재).
    통화 파싱은 numeric_consistency의 검증된 파서를 재사용한다.
    """
    if not text or ledger is None or len(ledger) == 0:
        return []
    from app.deep_research.agents.numeric_consistency import extract_mentions

    statements: list[str] = []
    seen_values: set[int] = set()
    for m in extract_mentions(text):
        if m.kind != "money" or m.currency != "USD":
            continue
        if m.value < _MIN_LEDGER_VALUE:
            continue
        key = round(m.value)
        if key in seen_values:
            continue
        # 라운드 값(정확히 $1M 단위)은 엄격 오차 — 우연 매치 억제
        tol = _ROUND_MATCH_TOL if m.value % 1_000_000 == 0 else _MATCH_TOL
        hits = ledger.match(m.value, tol=tol)
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
