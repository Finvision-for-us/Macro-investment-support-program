"""SEC 공시 연대기 소스 — 티커 기반 시계열 전수 수집 (쿼리 무관).

배경(2026-07-20 INDI 비교 감사): 검색 쿼리 기반 수집은 '검색에 걸린 사건'만
가져온다. 그 결과 8-K로 공시된 굵직한 사건들(분기 실적 PR의 $7.4B 백로그,
2029 전환사채, Wuxi 지분 매각)이 통째로 빠졌다. 이 소스는 검색을 거치지 않고
EDGAR 제출 이력 자체를 시간축으로 전수 수집한다:

1. ticker → CIK (SEC 공식 매핑 company_tickers.json, 프로세스 캐시)
2. data.sec.gov/submissions → 최근 N년 공시 목록 → '공시 연대기' 문서 1건
   (전 공시의 날짜·양식·8-K 항목 설명·원문 URL — 타임라인 뼈대)
3. 중요 항목(계약/인수매각/실적/채무/증권발행/기타사건) 8-K를 골라
   보도자료 첨부(EX-99) 우선으로 원문 추출 → 개별 문서

전부 공식 API·결정론(LLM 0콜), 키 불필요. 실패는 빈 리스트(파이프라인 계속).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.deep_research.config import SEC_USER_AGENT
from app.deep_research.models import ExtractedContent, SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

# 연대기에 싣는 양식 (Form 4 등 내부자 거래는 별도 경로가 있어 제외)
_CHRONICLE_FORMS = frozenset([
    "8-K", "8-K/A", "6-K", "10-K", "10-K/A", "10-Q", "10-Q/A",
    "S-1", "S-1/A", "S-3", "S-3/A", "424B5", "DEF 14A",
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
])

_FORM_DESC: dict[str, str] = {
    "8-K": "주요사건 보고", "8-K/A": "주요사건 보고(정정)",
    "6-K": "외국기업 수시보고", "10-K": "연차보고서", "10-K/A": "연차보고서(정정)",
    "10-Q": "분기보고서", "10-Q/A": "분기보고서(정정)",
    "S-1": "증권신고서", "S-1/A": "증권신고서(정정)",
    "S-3": "일괄증권신고서", "S-3/A": "일괄증권신고서(정정)",
    "424B5": "증권발행 설명서", "DEF 14A": "주주총회 위임장",
    "SC 13D": "대량보유 보고(경영참여)", "SC 13D/A": "대량보유 보고 변동",
    "SC 13G": "대량보유 보고(단순투자)", "SC 13G/A": "대량보유 보고 변동",
}

# 8-K 항목 코드 → 설명 (submissions API의 items 필드)
_ITEM_DESC: dict[str, str] = {
    "1.01": "중요 계약 체결", "1.02": "중요 계약 종료",
    "2.01": "자산 인수·매각 완료", "2.02": "실적 발표",
    "2.03": "채무·직접금융의무 발생", "2.05": "구조조정 비용",
    "2.06": "자산 손상", "3.01": "상장기준 미달 통지",
    "3.02": "미등록 증권 발행", "3.03": "주주권리 변경",
    "4.01": "감사인 변경", "5.01": "지배권 변동",
    "5.02": "임원·이사 선임/사임", "5.03": "정관 변경",
    "5.07": "주주총회 의결 결과", "7.01": "Regulation FD 공시",
    "8.01": "기타 중요 사건", "9.01": "재무제표·첨부문서",
}

# 원문 추출 대상으로 우선하는 8-K 항목 (사건성 높은 순)
_MATERIAL_ITEMS = frozenset([
    "1.01", "2.01", "2.02", "2.03", "2.05", "3.02", "5.01", "8.01",
])

_DOC_MAX_CHARS = 12_000  # 개별 원문 상한 — 자기검증 코퍼스(250k) 잠식 방지
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_NL_RE = re.compile(r"\n{3,}")

# ticker → (cik, company_title) 프로세스 캐시 (SEC 매핑은 사실상 정적)
_cik_cache: dict[str, tuple[str, str]] = {}


# ── 순수 함수 (테스트 대상, 네트워크 없음) ─────────────────────────

def _item_desc(items: str) -> str:
    """'1.01,9.01' → '중요 계약 체결(1.01), 재무제표·첨부문서(9.01)'."""
    parts = []
    for code in (items or "").split(","):
        code = code.strip()
        if not code:
            continue
        desc = _ITEM_DESC.get(code)
        parts.append(f"{desc}({code})" if desc else code)
    return ", ".join(parts)


def _filter_recent(
    recent: dict, years: int, now: Optional[datetime] = None,
) -> list[dict]:
    """submissions API의 recent 병렬배열 → 최근 N년 대상 양식만, 날짜 오름차순."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    items_list = recent.get("items", [])

    out: list[dict] = []
    for i, form in enumerate(forms):
        if form not in _CHRONICLE_FORMS:
            continue
        date = dates[i] if i < len(dates) else ""
        if not date or date < cutoff:
            continue
        out.append({
            "form": form,
            "date": date,
            "accession": (accs[i] if i < len(accs) else "").replace("-", ""),
            "primary_doc": docs[i] if i < len(docs) else "",
            "items": items_list[i] if i < len(items_list) else "",
        })
    out.sort(key=lambda f: f["date"])
    return out


def _doc_url(cik: str, filing: dict) -> str:
    cik_int = str(int(cik)) if cik.isdigit() else cik
    if filing.get("accession") and filing.get("primary_doc"):
        return (f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik_int}/{filing['accession']}/{filing['primary_doc']}")
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"


def build_chronicle(
    company: str, ticker: str, cik: str, filings: list[dict], years: int,
) -> str:
    """공시 목록 → 연대기 문서 텍스트 (타임라인 뼈대, 전 항목 원문 URL 포함)."""
    lines = [
        f"【SEC 공시 연대기 — {company} ({ticker}), CIK {cik}】",
        f"최근 {years}년 제출 공시 시간순 전체 목록 (총 {len(filings)}건).",
        "8-K는 중요 사건 발생 시 의무 제출되는 보고 — 아래 항목 설명이 사건 유형.",
        "",
    ]
    for f in filings:
        desc = _FORM_DESC.get(f["form"], "")
        line = f"{f['date']} | {f['form']}" + (f" ({desc})" if desc else "")
        if f["form"].startswith("8-K") and f.get("items"):
            item_str = _item_desc(f["items"])
            if item_str:
                line += f" — {item_str}"
        lines.append(line)
        lines.append(f"  원문: {_doc_url(cik, f)}")
    return "\n".join(lines)


def _select_targets(filings: list[dict], max_docs: int) -> list[dict]:
    """원문 추출 대상 선정 — 중요 항목 8-K(+6-K) 최신순, max_docs 상한."""
    def _is_material(f: dict) -> bool:
        if f["form"] in ("6-K",):
            return True
        if not f["form"].startswith("8-K"):
            return False
        codes = {c.strip() for c in (f.get("items") or "").split(",")}
        return bool(codes & _MATERIAL_ITEMS)

    material = [f for f in filings if _is_material(f)]
    material.sort(key=lambda f: f["date"], reverse=True)
    return material[:max_docs]


def _pick_exhibit(files: list[dict]) -> Optional[str]:
    """공시 index.json 파일 목록에서 보도자료 첨부(EX-99 htm) 우선 선택."""
    ex99 = [
        f["name"] for f in files
        if re.search(r"ex[-_]?99", f.get("name", ""), re.IGNORECASE)
        and f.get("name", "").lower().endswith((".htm", ".html"))
    ]
    return ex99[0] if ex99 else None


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(ln.strip() for ln in text.splitlines())
    text = _NL_RE.sub("\n\n", text).strip()
    return text[:_DOC_MAX_CHARS]


async def resolve_ticker(
    src: BaseSource, client, headers, ticker: str,
) -> Optional[tuple[str, str]]:
    """SEC 공식 ticker→(CIK, 회사명) 매핑 (정확 일치, 프로세스 캐시).

    ir_newsroom 등 다른 소스도 공식 회사명이 필요해 모듈 함수로 공유.
    """
    if not _cik_cache:
        resp = await src._get_with_retry(client, _TICKER_MAP_URL, headers=headers)
        if not resp or resp.status_code != 200:
            return None
        try:
            for entry in resp.json().values():
                tk = str(entry.get("ticker", "")).upper()
                if tk:
                    _cik_cache[tk] = (
                        str(entry.get("cik_str", "")),
                        str(entry.get("title", "")),
                    )
        except Exception as e:
            logger.warning(f"[filing_timeline] 티커 매핑 파싱 실패: {e}")
            return None
    return _cik_cache.get((ticker or "").upper())


# ── 소스 본체 ──────────────────────────────────────────────────────

class FilingTimelineSource(BaseSource):
    """티커 → EDGAR 공시 연대기 + 중요 8-K 원문. 검색 쿼리를 쓰지 않는다."""

    source_type = "filing_timeline"

    def is_available(self) -> bool:
        return True  # 공식 API, 키 불필요

    async def search(self, query: str, **kwargs) -> list[SearchResult]:
        return []  # 쿼리 소스 아님 — collect()가 진입점

    async def collect(
        self, ticker: str, years: int = 3, max_docs: int = 8,
    ) -> list[ExtractedContent]:
        """연대기 1건 + 중요 8-K 원문 문서들. 실패 시 빈 리스트."""
        ticker = (ticker or "").strip().upper()
        if not ticker:
            return []
        try:
            async with self._make_client() as client:
                headers = {"User-Agent": SEC_USER_AGENT}
                pair = await self._ticker_to_cik(client, headers, ticker)
                if not pair:
                    logger.info(f"[filing_timeline] {ticker} CIK 매핑 없음")
                    return []
                cik, company = pair

                url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
                resp = await self._get_with_retry(client, url, headers=headers)
                if not resp or resp.status_code != 200:
                    logger.warning(f"[filing_timeline] submissions 조회 실패: {ticker}")
                    return []
                recent = resp.json().get("filings", {}).get("recent", {})
                filings = _filter_recent(recent, years)
                if not filings:
                    logger.info(f"[filing_timeline] {ticker} 최근 {years}년 공시 없음")
                    return []

                chronicle = ExtractedContent(
                    url=(f"https://www.sec.gov/cgi-bin/browse-edgar?"
                         f"action=getcompany&CIK={cik}&type=8-K"),
                    title=f"[SEC 공시 연대기] {company} ({ticker}) 최근 {years}년",
                    content=build_chronicle(company, ticker, cik, filings, years),
                    domain="sec.gov",
                )
                chronicle.word_count = len(chronicle.content.split())

                targets = _select_targets(filings, max_docs)
                sem = asyncio.Semaphore(4)  # SEC 예의(10req/s 한도 내)

                async def _fetch(f: dict) -> Optional[ExtractedContent]:
                    async with sem:
                        return await self._fetch_filing_doc(
                            client, headers, cik, company, ticker, f)

                docs = await asyncio.gather(*[_fetch(f) for f in targets],
                                            return_exceptions=True)
                out = [chronicle] + [
                    d for d in docs if isinstance(d, ExtractedContent)
                ]
                logger.info(
                    f"[filing_timeline] {ticker}: 공시 {len(filings)}건 연대기 "
                    f"+ 원문 {len(out) - 1}/{len(targets)}건"
                )
                return out
        except Exception as e:
            logger.warning(f"[filing_timeline] {ticker} 수집 예외: {e}")
            return []

    async def _ticker_to_cik(
        self, client, headers, ticker: str,
    ) -> Optional[tuple[str, str]]:
        return await resolve_ticker(self, client, headers, ticker)

    async def _fetch_filing_doc(
        self, client, headers, cik: str, company: str, ticker: str, f: dict,
    ) -> Optional[ExtractedContent]:
        """공시 1건의 원문 — EX-99(보도자료) 우선, 없으면 본문 문서."""
        cik_int = str(int(cik)) if cik.isdigit() else cik
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{f['accession']}"
        doc_name = f.get("primary_doc", "")

        # index.json에서 보도자료 첨부 탐색 (실적 수치는 대개 EX-99.1에 있다)
        try:
            idx = await self._get_with_retry(
                client, f"{base}/index.json", headers=headers)
            if idx and idx.status_code == 200:
                files = idx.json().get("directory", {}).get("item", [])
                exhibit = _pick_exhibit(files)
                if exhibit:
                    doc_name = exhibit
        except Exception:
            pass  # 본문 문서로 폴백

        if not doc_name:
            return None
        resp = await self._get_with_retry(
            client, f"{base}/{doc_name}", headers=headers)
        if not resp or resp.status_code != 200:
            return None
        text = _strip_html(resp.text)
        if len(text) < 200:
            return None

        item_str = _item_desc(f.get("items", "")) or _FORM_DESC.get(f["form"], "")
        title = f"[SEC {f['form']} {f['date']}] {company} ({ticker})"
        if item_str:
            title += f" — {item_str}"
        return ExtractedContent(
            url=f"{base}/{doc_name}",
            title=title,
            content=f"【SEC {f['form']} 공시 원문 | 제출일 {f['date']}】\n{text}",
            domain="sec.gov",
            word_count=len(text.split()),
        )


filing_timeline_source = FilingTimelineSource()
