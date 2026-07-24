"""방어선 6: 보고서 출력 후처리 — synthesizer 산출물의 3대 표시 결함 정정.

2026-07-20 INDI 리포트 감사에서 확인된 결함(전부 LLM 산출물의 표시 품질 문제,
사실관계와 무관):

1. 각주 무결성 — 본문이 인용한 [n] 각주 번호가 소스 목록에 없어 독자가 추적
   불가(감사 실측: [12] €40M 주장의 각주가 목록에 부재). 원인은 LLM이 수집
   문서 '본문 안'에 있던 URL을 [source:URL]로 인용했는데 그 URL은 최상위
   수집 소스가 아니라 소스 목록에 안 실린 것. → 인용된 URL을 소스 목록에
   편입(도메인 신뢰도 스코어)해 모든 [n]이 목록 항목으로 해소되게 하고,
   그래도 해소 안 되는 댕글링 [n]은 제거(깨진 참조 노출 금지).

2. 태그 위치 — [unverified]/[추론] 태그가 숫자-단위 사이에 끼어 표시가 깨짐
   (감사 실측: "12[unverified]개월"). → 토큰 중간에 낀 태그를 그 토큰
   맨 앞으로 이동("[unverified] 12개월").

3. 깨진 문장 잔재 — 토큰 제거 후 남은 이중 공백·빈 괄호, 한글 사이에 낀
   라틴 기능어(감사 실측: "규모 of 신규"). → 안전 범위만 정리하고, 임의의
   한글 손상("메모머드급" 등)은 결정론 복구가 불가능하므로 손대지 않고
   탐지 카운트만 로깅(정직성 — 못 고치는 건 고친 척하지 않는다).

원칙: LLM 무관여 순수 문자열 변환. 의미를 추가/변경하지 않는다. 손대면
확실히 개선되는 것만 건드리고, 애매하면 원문 보존.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.deep_research.agents.evidence_ranker import score_url
from app.deep_research.common import domain_of
from app.deep_research.models import SourceInfo

logger = logging.getLogger(__name__)

# ── 결함 2: 태그 위치 ──
# 태그(단일/이중 대괄호) — 토큰 내부(비공백에 인접)에 끼면 앞으로 이동
_TAG_INNER = r"\[?\[(?:unverified|추론)\]\]?"
# 하나의 공백구분 토큰 전체를 잡되, 그 안에 태그가 낀 경우: lead + 태그 + trail
_TOKEN_WITH_TAG = re.compile(
    rf"(?<!\S)(?P<lead>\S*?)(?P<tag>{_TAG_INNER})(?P<trail>\S*)")

# ── 결함 3: 안전 정리 ──
# 한글 사이에 낀 라틴 기능어(문장이 영·한 혼합으로 깨진 흔적)
_LATIN_FILLER = re.compile(
    r"(?<=[가-힣])\s+(?:of|the|and|or|in|on|to|for|with|by|at|as|is|are)\s+"
    r"(?=[가-힣])")
_EMPTY_BRACKETS = re.compile(r"\[\s*\]|\(\s*\)|\{\s*\}")
_SPACE_BEFORE_PUNCT = re.compile(r"(?<=[가-힣])\s+([,.](?!\d))")  # 숫자 소수점 보존
_MULTISPACE = re.compile(r"[^\S\n]{2,}")

# ── 각주 토큰 ──
_SOURCE_TOKEN = re.compile(r"\[source:\s*(https?://[^\]]+)\]")
_NUM_REF = re.compile(r"\[(\d{1,3})\]")

# ── 탐지(수리 불가, 로깅용) ──
_MIDLINE_PLACEHOLDER = re.compile(r"원본\s*확인\s*필요[을를이가은는의로]")


# ─────────────────────────────────────────────────────────────
# 결함 1: 각주 무결성
# ─────────────────────────────────────────────────────────────

def ensure_cited_sources(
    sources: list[SourceInfo], url_to_num: dict[str, int],
) -> list[SourceInfo]:
    """본문이 인용한 URL이 소스 목록에 없으면 편입(각주 번호순, 도메인 스코어).

    댕글링 각주([n]인데 목록에 없음)의 근본 차단. 반환은 새 리스트.
    """
    existing = {s.url for s in sources}
    out = list(sources)
    for url, _num in sorted(url_to_num.items(), key=lambda kv: kv[1]):
        if url in existing:
            continue
        _, credibility = score_url(url)
        out.append(SourceInfo(
            url=url, title=domain_of(url) or url,
            domain=domain_of(url), credibility=credibility,
        ))
        existing.add(url)
    return out


def assign_ref_numbers(
    sources: list[SourceInfo], url_to_num: dict[str, int],
) -> None:
    """소스 목록에 각주 번호를 부여(in-place)."""
    for s in sources:
        num = url_to_num.get(s.url)
        if num is not None:
            s.ref_number = num


def _normalize_and_strip_refs(
    text: str, url_to_num: dict[str, int], valid_nums: set[int],
) -> tuple[str, int]:
    """검증 패스가 되살린 [source:URL]을 [n]으로 정규화하고, 소스 목록에
    없는 댕글링 [n]을 제거. 반환 (정리된 텍스트, 제거된 댕글링 수)."""
    if not text:
        return text, 0

    def _src_to_num(m: re.Match) -> str:
        num = url_to_num.get(m.group(1).strip())
        return f"[{num}]" if num in valid_nums else ""

    text = _SOURCE_TOKEN.sub(_src_to_num, text)

    dangling = 0

    def _strip_dangling(m: re.Match) -> str:
        nonlocal dangling
        if int(m.group(1)) in valid_nums:
            return m.group(0)
        dangling += 1
        return ""

    text = _NUM_REF.sub(_strip_dangling, text)
    return text, dangling


# ─────────────────────────────────────────────────────────────
# 결함 2: 태그 위치
# ─────────────────────────────────────────────────────────────

def relocate_misplaced_tags(text: str) -> tuple[str, int]:
    """토큰 중간/끝에 낀 [unverified]·[추론] 태그를 토큰 맨 앞으로 이동.

    "12[unverified]개월" → "[unverified] 12개월"
    "12개월[unverified]" → "[unverified] 12개월"
    "[unverified]개월"   → "[unverified] 개월"
    이미 올바른 "[unverified] 개월"(공백 뒤 단독)은 불변.
    반환 (정리된 텍스트, 이동 횟수).
    """
    if not text:
        return text, 0
    moved = 0

    def _fix(m: re.Match) -> str:
        nonlocal moved
        lead, tag, trail = m.group("lead"), m.group("tag"), m.group("trail")
        rest = lead + trail
        if not rest:
            return tag  # 단독 태그 토큰 — 불변
        moved += 1
        return f"{tag} {rest}"

    return _TOKEN_WITH_TAG.sub(_fix, text), moved


# ─────────────────────────────────────────────────────────────
# 결함 3: 안전 정리 + 탐지
# ─────────────────────────────────────────────────────────────

def clean_broken_artifacts(text: str) -> str:
    """토큰 제거 잔재·한글 사이 라틴 기능어 등 '확실히 개선되는' 것만 정리.

    임의의 한글 손상은 손대지 않는다(복구 불가 — 탐지는 detect_suspects).
    """
    if not text:
        return text
    text = _LATIN_FILLER.sub(" ", text)
    text = _EMPTY_BRACKETS.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTISPACE.sub(" ", text)
    # 개행 앞뒤 공백 정리
    text = re.sub(r"[^\S\n]+\n", "\n", text)
    text = re.sub(r"\n[^\S\n]+", "\n", text)
    return text.strip()


def detect_suspects(text: str) -> int:
    """복구 불가한 깨진 흔적 개수(로깅·품질지표용). 텍스트는 변경 안 함."""
    if not text:
        return 0
    return len(_MIDLINE_PLACEHOLDER.findall(text))


# ─────────────────────────────────────────────────────────────
# 오케스트레이터
# ─────────────────────────────────────────────────────────────

def _sanitize_text(
    text: str, url_to_num: dict[str, int], valid_nums: set[int],
) -> tuple[str, dict]:
    stats = {"relocated": 0, "dangling": 0, "suspects": 0}
    if not isinstance(text, str) or not text:
        return text, stats
    stats["suspects"] = detect_suspects(text)
    text, dangling = _normalize_and_strip_refs(text, url_to_num, valid_nums)
    text, moved = relocate_misplaced_tags(text)
    text = clean_broken_artifacts(text)
    stats["relocated"] = moved
    stats["dangling"] = dangling
    return text, stats


def reconcile_and_sanitize(
    data: dict,
    sources: list[SourceInfo],
    url_to_num: Optional[dict[str, int]] = None,
) -> tuple[dict, dict]:
    """최종 data의 텍스트 필드 전체에 각주 정규화 + 태그 이동 + 안전 정리.

    호출 전 ensure_cited_sources/assign_ref_numbers로 소스 목록·번호가 확정돼
    있어야 한다(valid_nums를 소스의 ref_number로 산출). 반환 (data, 통계).
    """
    url_to_num = url_to_num or {}
    valid_nums = {s.ref_number for s in sources if s.ref_number is not None}
    agg = {"relocated": 0, "dangling": 0, "suspects": 0}

    def _apply(text):
        cleaned, st = _sanitize_text(text, url_to_num, valid_nums)
        for k in agg:
            agg[k] += st[k]
        return cleaned

    if isinstance(data.get("summary"), str):
        data["summary"] = _apply(data["summary"])
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and isinstance(sec.get("content"), str):
            sec["content"] = _apply(sec["content"])
    for kf in data.get("key_findings") or []:
        if isinstance(kf, dict) and isinstance(kf.get("finding"), str):
            kf["finding"] = _apply(kf["finding"])
    for t in data.get("timeline") or []:
        if isinstance(t, dict) and isinstance(t.get("event"), str):
            t["event"] = _apply(t["event"])

    if any(agg.values()):
        logger.info(
            f"[sanitize] 각주 댕글링 제거 {agg['dangling']} / "
            f"태그 재배치 {agg['relocated']} / 미복구 흔적 {agg['suspects']}")
    return data, agg
