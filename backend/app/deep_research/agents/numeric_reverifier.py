"""수치 전용 2차 재검증 — [unverified] 태그를 전체 수집 원문과 결정론 대조로 해소.

배경: 자기검증(LLM) 패스는 컨텍스트 제한 때문에 원문 일부만 보고 태깅한다.
그 결과 수치가 실제로 소스에 존재해도 컨텍스트 밖이면 [unverified] 처리
(2026-07-20 4차 시험 실측: 태그 114개의 대부분이 연도·금액·주식수).
이 모듈은 태그가 붙은 구간의 수치를 **전체 수집 원문**(무제한)과 대조해,
전 수치가 확인되면 태그를 제거한다 — LLM 0콜, 결정론.

원칙(무할루시네이션 보수성):
- 수치가 하나도 없는 태그 구간은 손대지 않는다(사실관계 판단은 LLM 검증 존중).
- 구간의 수치 '전부'가 원문에서 확인될 때만 태그 해제(부분 확인은 유지).
- 금액은 numeric_consistency의 다국어 파서(조/억/万/billion)로 정규화 후
  통화 일치 + 1% 오차 내 매칭. 연도는 문자열 존재, %는 절대 0.1%p 오차.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.deep_research.agents.numeric_consistency import extract_mentions

logger = logging.getLogger(__name__)

# 태그와 그 뒤 주장 구간: 다음 태그/문장 경계/개행 전까지 (최대 160자)
_TAG_RE = re.compile(r"\[?\[unverified\]\]?\s*")
# \b는 한글이 바로 붙는 "2021년"에서 실패(한글=\w) — 숫자 경계로 대체
_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
_PCT_RE = re.compile(r"([\d.]+)\s*%")
# 통화 없는 큰 수: "1억 1,800만 주", "960,834,355" 등
_BARE_SCALED_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*억(?:\s*([\d,]+(?:\.\d+)?)\s*만)?")
_BARE_BIG_RE = re.compile(r"\b(\d{1,3}(?:,\d{3}){2,})\b")  # 1,000,000+

_CLAIM_MAX = 160


def _to_f(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _claim_span(text: str, tag_end: int) -> str:
    """태그 직후 주장 구간 — 다음 태그 또는 문장 경계까지."""
    rest = text[tag_end:tag_end + _CLAIM_MAX]
    nxt = _TAG_RE.search(rest)
    if nxt:
        rest = rest[:nxt.start()]
    # 문장 경계(마침표+공백/개행)에서 컷 — 소수점(3.5)은 보존
    m = re.search(r"[.。!?]\s|\n", rest)
    if m:
        rest = rest[:m.start() + 1]
    return rest


def _numbers_in_claim(claim: str) -> dict:
    """주장 구간의 수치 분해: 금액(정규화), 연도, %, 통화없는 큰 수."""
    money = [(m.value, m.currency) for m in extract_mentions(claim) if m.kind == "money"]
    pcts = [_to_f(x) for x in _PCT_RE.findall(claim)]
    years = set(_YEAR_RE.findall(claim))
    bare: list[float] = []
    for m in _BARE_SCALED_RE.finditer(claim):
        eok, man = _to_f(m.group(1)), _to_f(m.group(2)) if m.group(2) else 0.0
        if eok is not None:
            bare.append(eok * 1e8 + (man or 0.0) * 1e4)
    for s in _BARE_BIG_RE.findall(claim):
        v = _to_f(s)
        if v is not None:
            bare.append(v)
    return {"money": money, "pcts": [p for p in pcts if p is not None],
            "years": years, "bare": bare}


class CorpusIndex:
    """전체 수집 원문의 수치 인덱스 — 한 번 구축해 다회 대조."""

    def __init__(self, corpus: str):
        self._text = corpus
        mentions = extract_mentions(corpus)
        self._money = [(m.value, m.currency) for m in mentions if m.kind == "money"]
        self._pcts = {round(p, 2) for p in
                      (_to_f(x) for x in _PCT_RE.findall(corpus)) if p is not None}
        self._bare: set[float] = set()
        for m in _BARE_SCALED_RE.finditer(corpus):
            eok, man = _to_f(m.group(1)), _to_f(m.group(2)) if m.group(2) else 0.0
            if eok is not None:
                self._bare.add(eok * 1e8 + (man or 0.0) * 1e4)
        for s in _BARE_BIG_RE.findall(corpus):
            v = _to_f(s)
            if v is not None:
                self._bare.add(v)

    def _money_ok(self, value: float, currency: Optional[str]) -> bool:
        for cv, cc in self._money:
            if currency and cc and currency != cc:
                continue
            if cv > 0 and abs(cv - value) / max(cv, value) <= 0.01:
                return True
        # 통화 표기 없이 원시 숫자로만 존재하는 경우도 인정 (예: 표 안의 7,400,000,000)
        return any(v > 0 and abs(v - value) / max(v, value) <= 0.01 for v in self._bare)

    def _bare_ok(self, value: float) -> bool:
        if any(v > 0 and abs(v - value) / max(v, value) <= 0.01 for v in self._bare):
            return True
        return any(cv > 0 and abs(cv - value) / max(cv, value) <= 0.01
                   for cv, _ in self._money)

    def claim_confirmed(self, claim: str) -> Optional[bool]:
        """주장 구간 판정. None=수치 없음(판단 불가), True=전수 확인, False=미확인 존재."""
        nums = _numbers_in_claim(claim)
        total = len(nums["money"]) + len(nums["pcts"]) + len(nums["years"]) + len(nums["bare"])
        if total == 0:
            return None
        for value, cur in nums["money"]:
            if not self._money_ok(value, cur):
                return False
        for p in nums["pcts"]:
            if round(p, 2) not in self._pcts and not any(
                    abs(p - q) <= 0.1 for q in self._pcts):
                return False
        for y in nums["years"]:
            if y not in self._text:
                return False
        for v in nums["bare"]:
            if not self._bare_ok(v):
                return False
        return True


def _reverify_text(text: str, idx: CorpusIndex) -> tuple[str, int, int]:
    """텍스트 하나에서 확인된 수치 태그 제거 → (새 텍스트, 해제 수, 유지 수)."""
    removed = kept = 0
    out: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        out.append(text[pos:m.start()])
        claim = _claim_span(text, m.end())
        verdict = idx.claim_confirmed(claim)
        if verdict is True:
            removed += 1  # 태그 삭제(추가 안 함)
        else:
            kept += 1
            out.append(m.group(0))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), removed, kept


def reverify_report(data: dict, corpus: str) -> tuple[dict, int, int]:
    """리포트 dict의 텍스트 필드 전체에 수치 재검증 적용."""
    if not corpus or "[unverified]" not in str(data):
        return data, 0, 0
    idx = CorpusIndex(corpus)
    total_removed = total_kept = 0

    def _fix(s):
        nonlocal total_removed, total_kept
        if not isinstance(s, str) or "unverified" not in s:
            return s
        new, r, k = _reverify_text(s, idx)
        total_removed += r
        total_kept += k
        return new

    for key in ("summary",):
        if key in data:
            data[key] = _fix(data[key])
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and "content" in sec:
            sec["content"] = _fix(sec["content"])
        if isinstance(sec, dict) and "title" in sec:
            sec["title"] = _fix(sec["title"])
    for kf in data.get("key_findings") or []:
        if isinstance(kf, dict) and "finding" in kf:
            kf["finding"] = _fix(kf["finding"])
    cv = data.get("cross_validation")
    if isinstance(cv, list):
        data["cross_validation"] = [_fix(x) for x in cv]
    tl = data.get("timeline") or []
    for t in tl:
        if isinstance(t, dict) and "event" in t:
            t["event"] = _fix(t["event"])

    if total_removed or total_kept:
        logger.info(f"[reverify] 수치 재검증: 태그 해제 {total_removed} / 유지 {total_kept}")
    return data, total_removed, total_kept
