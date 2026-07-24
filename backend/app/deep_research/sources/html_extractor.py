"""로컬 HTML 본문 추출 — Jina Reader 폴백/보완 (trafilatura 기반).

배경: 본문 추출이 Jina Reader(무키 20 RPM)에 의존하는데, 소스가 100건+로
늘면서 503 폭주로 대량 실패 → 자기검증이 근거 본문을 못 찾아 [[unverified]]
태그가 남발됨(2026-07-19 2차 시험 실측: 태그 175개, GlobalFoundries 같은
실사실까지 미확인 처리). 이 모듈은 httpx + trafilatura로 로컬 추출해
Jina 실패분을 흡수한다.

의존성·폴백 계약: trafilatura 미설치 시 모듈 비활성(빈 결과) — 기존
Jina 단독 경로 그대로. 다운로드/파싱 실패는 건너뜀, 파이프라인 무사.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.deep_research.common import domain_of
from app.deep_research.models import ExtractedContent
from app.deep_research.sources.pdf_extractor import _word_count

logger = logging.getLogger(__name__)

try:
    import trafilatura as _traf
    _TRAF_OK = True
except ImportError:
    _TRAF_OK = False
    logger.warning("[html] trafilatura 미설치 — 로컬 HTML 추출 비활성(Jina 단독)")

_TIMEOUT = 15.0
_MAX_HTML_BYTES = 3 * 1024 * 1024   # 3MB 초과 HTML 스킵
# 2단 UA: 브라우저형 → 403이면 정직한 봇 UA 재시도.
# (위키미디어 등은 'python TLS + 브라우저 UA'를 스푸핑으로 차단하지만
#  정직한 봇 UA는 허용. Cloudflare/Akamai TLS 지문 차단 사이트는 둘 다
#  안 통함 — 그쪽은 Jina Reader 몫.)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_BOT_UA = "FinVisionResearch/1.0 (financial research; admin@finvision.app)"


def _extract_sync(html: str, url: str) -> Optional[ExtractedContent]:
    """HTML → 본문 (trafilatura, CPU 바운드)."""
    try:
        text = _traf.extract(html, url=url, include_comments=False,
                             include_tables=True) or ""
    except Exception:
        return None
    text = text.strip()
    if len(text) < 200:
        return None
    meta_title = ""
    try:
        md = _traf.extract_metadata(html)
        meta_title = (md.title or "") if md else ""
    except Exception:
        pass
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return ExtractedContent(
        url=url,
        title=(meta_title or first_line)[:120],
        content=text[:50000],
        domain=domain_of(url),
        word_count=_word_count(text),
    )


async def extract_html(url: str) -> Optional[ExtractedContent]:
    """URL 다운로드 → 로컬 본문 추출. 어떤 실패도 None."""
    if not _TRAF_OK:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"})
            if resp.status_code == 403:  # 스푸핑 차단류 → 정직한 봇 UA 재시도
                resp = await client.get(url, headers={"User-Agent": _BOT_UA})
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype and ctype:
                return None
            if len(resp.content) > _MAX_HTML_BYTES:
                return None
            html = resp.text
        return await asyncio.to_thread(_extract_sync, html, url)
    except Exception:
        return None


async def extract_html_batch(urls: list[str], max_concurrent: int = 8) -> list[ExtractedContent]:
    """여러 URL 병렬 로컬 추출."""
    if not _TRAF_OK or not urls:
        return []
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(u: str) -> Optional[ExtractedContent]:
        async with sem:
            return await extract_html(u)

    results = await asyncio.gather(*(_one(u) for u in urls), return_exceptions=True)
    ok = [r for r in results if isinstance(r, ExtractedContent)]
    if ok:
        logger.info(f"[html] 로컬 추출 {len(ok)}/{len(urls)}건")
    return ok
