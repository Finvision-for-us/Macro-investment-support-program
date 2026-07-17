"""cninfo 공시 소스 (akshare) — 중국 A주 공시 목록을 1차 자료로 직접 수집.

미러 탐색의 중국 축: 웹 검색(tavily/parallel)이 놓치는 중국 공시 원문을
거래소 공시 플랫폼(巨潮资讯 cninfo)에서 목록 API로 직접 가져온다.
공시 링크는 정적 PDF(static.cninfo.com.cn/finalpage/{날짜}/{공고ID}.PDF)로
변환해 반환 — 추출 단계의 로컬 PDF 2단 추출(텍스트레이어→OCR)과 직결된다
(구형 스캔 공시도 OCR로 읽힘. 패턴·OCR 모두 라이브 실측 확정).

활성 조건: 쿼리/컨텍스트에 A주 6자리 종목코드가 있을 때만(예: lead_follower가
'301112' 단서를 발견해 보완 쿼리에 포함한 경우, context.cn_ticker 명시).
코드 없이 회사명만으로는 검색하지 않는다 — 잘못된 종목의 공시를 '원문'으로
주입하는 사고(무할루시네이션 위반)를 막기 위해 보수적으로 설계.

의존성·폴백 계약: akshare 미설치/조회 실패 시 빈 결과 + 경고 로그.
akshare는 임포트가 무겁다(수 초) — 지연 임포트 + to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import parse_qs, urlparse

from app.deep_research.models import SearchResult

logger = logging.getLogger(__name__)

_CNINFO_PDF = "http://static.cninfo.com.cn/finalpage/{date}/{aid}.PDF"
_LOOKBACK_DAYS = 550          # 최근 ~18개월 공시
_MAX_CODES = 2                # 쿼리당 조회할 종목코드 상한 (akshare 호출 비용)
_MAX_RESULTS_PER_CODE = 8     # 코드당 반환 공시 상한
_KEYWORD_TOP = 8              # 키워드 매칭 시 상위 N
_RECENT_FALLBACK = 5          # 키워드 무매칭 시 최신 N

# A주 6자리 코드: 심천 00x/30x(창업판), 상해 60x/68x(과창판), 북증 8xx.
# '19'/'20' 시작은 연월(202606 등) 오탐 위험이 커 제외(B주 200xxx는 희귀 — 트레이드오프).
_A_SHARE_CODE_RE = re.compile(r"\b(00\d{4}|30\d{4}|60\d{4}|68\d{4}|8\d{5})\b")

_akshare_checked = False
_akshare_ok = False


def _akshare_available() -> bool:
    global _akshare_checked, _akshare_ok
    if _akshare_checked:
        return _akshare_ok
    _akshare_checked = True
    try:
        import akshare  # noqa: F401  (지연 임포트 — 수 초 걸림)
        _akshare_ok = True
    except Exception as e:
        logger.warning(f"[cninfo] akshare 불가 — 중국 공시 소스 비활성: {e}")
        _akshare_ok = False
    return _akshare_ok


def extract_a_share_codes(text: str) -> list[str]:
    """텍스트에서 A주 6자리 종목코드 후보 추출(순서 유지·중복 제거)."""
    return list(dict.fromkeys(_A_SHARE_CODE_RE.findall(text or "")))


def _fetch_disclosures_sync(code: str, start: str, end: str) -> list[dict]:
    """akshare 공시 목록 조회(동기) → [{title, time, link}]. 실패는 호출부에서 처리."""
    import akshare as ak
    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=code, market="沪深京", start_date=start, end_date=end,
    )
    rows: list[dict] = []
    for _, r in df.iterrows():
        rows.append({
            "title": str(r.get("公告标题") or ""),
            "time": str(r.get("公告时间") or ""),
            "link": str(r.get("公告链接") or ""),
        })
    return rows


def _pdf_url_from_link(link: str, time_str: str) -> Optional[str]:
    """공시 상세 링크 → 정적 PDF URL.

    detail?...announcementId=...&announcementTime=YYYY-MM-DD 형식에서 파싱
    (컬럼명 변화에 견고하도록 링크의 쿼리 파라미터를 신뢰).
    """
    try:
        qs = parse_qs(urlparse(link).query)
        aid = (qs.get("announcementId") or [""])[0]
        date = (qs.get("announcementTime") or [""])[0] or (time_str or "")[:10]
        if aid.isdigit() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
            return _CNINFO_PDF.format(date=date, aid=aid)
    except Exception:
        pass
    return None


def _query_tokens(query: str) -> set[str]:
    """랭킹용 토큰: Latin 단어(3자+) 소문자 + CJK 2-그램."""
    tokens = {t.lower() for t in re.findall(r"[A-Za-z]{3,}", query or "")}
    cjk = re.findall(r"[一-鿿]", query or "")
    tokens.update("".join(p) for p in zip(cjk, cjk[1:]))
    return tokens


def _rank_rows(rows: list[dict], query: str) -> list[dict]:
    """공시 제목의 쿼리 토큰 겹침으로 정렬. 매칭 있으면 상위 N, 없으면 최신 N."""
    tokens = _query_tokens(query)

    def _overlap(row: dict) -> int:
        title = row["title"].lower()
        return sum(1 for t in tokens if t in title)

    scored = [(_overlap(r), r) for r in rows]
    matched = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s > 0]
    if matched:
        return matched[:_KEYWORD_TOP]
    # 무매칭 → 최신순(시간 문자열 내림차순) 소수만 — 최근 공시는 그 자체로 맥락 가치
    return sorted(rows, key=lambda r: r["time"], reverse=True)[:_RECENT_FALLBACK]


class CninfoDisclosureSource:
    """A주 종목코드 기반 cninfo 공시 목록 → SearchResult(정적 PDF URL)."""

    source_type = "cninfo"

    def is_available(self) -> bool:
        return _akshare_available()

    async def search_disclosures(
        self,
        query: str,
        context: Optional[dict] = None,
        max_results: int = _MAX_RESULTS_PER_CODE * _MAX_CODES,
    ) -> list[SearchResult]:
        """쿼리/컨텍스트의 A주 코드로 공시를 조회. 코드 없으면 빈 결과(보수적)."""
        if not self.is_available():
            return []
        codes = extract_a_share_codes(query)
        ctx_code = str((context or {}).get("cn_ticker") or "").strip()
        if ctx_code and _A_SHARE_CODE_RE.fullmatch(ctx_code):
            codes = list(dict.fromkeys([ctx_code] + codes))
        if not codes:
            return []
        codes = codes[:_MAX_CODES]

        end = datetime.now()
        start_s = (end - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        out: list[SearchResult] = []
        for code in codes:
            try:
                rows = await asyncio.to_thread(
                    _fetch_disclosures_sync, code, start_s, end_s
                )
            except Exception as e:
                logger.warning(f"[cninfo] 공시 조회 실패({code}, 무시): {e}")
                continue
            picked = _rank_rows(rows, query)
            for row in picked[:_MAX_RESULTS_PER_CODE]:
                pdf = _pdf_url_from_link(row["link"], row["time"])
                if not pdf:
                    continue
                out.append(SearchResult(
                    url=pdf,
                    title=f"[{code}] {row['title']}",
                    content=f"cninfo 공시 {row['time']}: {row['title']}",
                    source_type="official",
                    relevance_score=0.75,   # 거래소 공시 원문 — 공식 티어
                    published_date=row["time"][:10] or None,
                ))
            if rows:
                logger.info(
                    f"[cninfo] {code}: 공시 {len(rows)}건 중 {len(picked)}건 선별"
                )
        return out[:max_results]


# 싱글턴 (다른 소스들과 동일 패턴)
cninfo_disclosure_source = CninfoDisclosureSource()
