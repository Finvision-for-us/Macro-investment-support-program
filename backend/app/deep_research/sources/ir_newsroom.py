"""IR 뉴스룸 소스 — 회사 보도자료 목록 페이지 시계열 순회 (쿼리 무관).

배경(2026-07-20 INDI 비교 감사): 8-K 연대기(filing_timeline)로도 안 잡히는
격차가 남는다 — 제품 출시(iND881)·파트너십(Ficosa)·첫 출하 같은 PR은 8-K
의무 대상이 아니라 IR 뉴스룸에만 실린다. 타 AI의 커버리지 우위 원천이
뉴스룸 시계열 순회였음이 인용 패턴으로 확인됐다.

동작:
1. 회사명(SEC 공식 매핑 재사용) + 검색 1회로 IR 뉴스 목록 페이지 후보 탐색
   (회사 도메인·news/press 경로 가점, 통신사 애그리게이터 감점)
2. 후보 상위를 Jina로 읽어 날짜+제목+링크를 결정론 파싱
   (실패 시에만 lite LLM 폴백 — JSON 추출)
3. '뉴스 연대기' 문서 1건 + 최신 PR들을 추출 대상(SearchResult)으로 반환
   → 파이프라인의 기존 추출 스택(Jina/로컬 HTML)이 원문 수집

실패는 (None, []) — 파이프라인 계속.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from app.deep_research import llm_client
from app.deep_research.config import GEMINI_LITE_MODEL
from app.deep_research.models import ExtractedContent, SearchResult
from app.deep_research.sources.base import BaseSource
from app.deep_research.sources.filing_timeline import resolve_ticker
from app.deep_research.sources.jina_reader import JinaReaderSource

logger = logging.getLogger(__name__)

_SEC_HEADERS = {"User-Agent": "FinVisionResearch research@finvision.local"}

# 회사 자체 사이트가 아닌 통신사/포털 — 목록 페이지 후보에서 감점
_AGGREGATOR_HOSTS = (
    "prnewswire.", "globenewswire.", "businesswire.", "accesswire.",
    "seekingalpha.", "yahoo.", "marketwatch.", "stocktitan.", "nasdaq.com",
    "benzinga.", "investing.com", "fool.com", "wikipedia.", "sec.gov",
    "bloomberg.", "reuters.", "wsj.com", "cnbc.",
)

# 링크 제외 대상 (뉴스 항목이 아닌 것)
_SKIP_URL_RE = re.compile(
    r"(twitter\.|x\.com/|facebook\.|linkedin\.|youtube\.|instagram\.|"
    r"mailto:|javascript:|\.(png|jpe?g|svg|gif|css|js)($|\?))", re.IGNORECASE)

_LINK_RE = re.compile(r"\[([^\]\[]{6,250})\]\((https?://[^)\s]+)\)")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

_DATE_MDY_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(20\d{2})\b")
_DATE_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(20\d{2})\b")
_DATE_ISO_RE = re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_DATE_US_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")

# 회사명 토큰화 시 무시할 일반어
_NAME_STOPWORDS = frozenset([
    "semiconductor", "semiconductors", "inc", "corp", "corporation",
    "company", "technologies", "technology", "holdings", "holding",
    "group", "international", "limited", "the", "and",
])


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────

def _iso(y: int, m: int, d: int) -> Optional[str]:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _find_date(line: str) -> Optional[str]:
    """한 줄에서 첫 날짜를 ISO로 — 'June 10, 2026'/'10 Jun 2026'/ISO/미국식."""
    m = _DATE_MDY_RE.search(line)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()],
                    int(m.group(2)))
    m = _DATE_DMY_RE.search(line)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()],
                    int(m.group(1)))
    m = _DATE_ISO_RE.search(line)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_US_RE.search(line)
    if m:  # 미국 IR 관례 MM/DD/YYYY
        return _iso(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None


def parse_news_items(
    markdown: str, years: int, now: Optional[datetime] = None,
) -> list[dict]:
    """Jina 마크다운 목록 → [{date, title, url}] 날짜 오름차순.

    날짜-링크 페어링: 같은 줄 우선, 없으면 앞뒤 3줄 중 가장 가까운 날짜.
    내비게이션 링크(짧은 제목)·소셜/이미지 링크 제외, 같은 PR의
    HTML/PDF 중복 링크(Q4 플랫폼 'Download …')는 1건으로(HTML 우선).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    lines = markdown.splitlines()
    line_dates = {i: d for i, ln in enumerate(lines)
                  if (d := _find_date(ln))}

    items: list[dict] = []
    seen: set[str] = set()
    for i, ln in enumerate(lines):
        for m in _LINK_RE.finditer(ln):
            title = m.group(1).strip().strip("*").strip()
            url = m.group(2).strip()
            if url in seen or _SKIP_URL_RE.search(url):
                continue
            if len(title) < 18 or len(title.split()) < 3:
                continue  # 내비게이션/버튼 링크
            # 같은 줄 우선, 없으면 직전 3줄/직후 2줄 중 '가장 가까운' 날짜.
            # (거리 동률이면 직전 줄 — 날짜가 제목 위에 오는 레이아웃이 다수)
            date = line_dates.get(i)
            if not date:
                best_dist = None
                for j in range(max(0, i - 3), min(len(lines), i + 3)):
                    if j == i or j not in line_dates:
                        continue
                    dist = abs(j - i) * 2 + (1 if j > i else 0)
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        date = line_dates[j]
            if not date or date < cutoff:
                continue
            seen.add(url)
            items.append({"date": date, "title": title, "url": url})

    # 같은 (날짜, 정규화 제목) → 1건. PDF 'Download' 중복은 본문 링크 우선.
    dedup: dict[tuple[str, str], dict] = {}
    for it in items:
        key = (it["date"], _canon_title(it["title"]))
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = it
        elif prev["url"].lower().endswith(".pdf") and \
                not it["url"].lower().endswith(".pdf"):
            dedup[key] = it
    out = list(dedup.values())
    # 표시 제목도 정리 (PDF 링크만 남은 경우의 'Download, …' 접두)
    for it in out:
        it["title"] = re.sub(r"^Download,?\s*", "", it["title"]).strip()
    out.sort(key=lambda it: it["date"])
    return out


def _canon_title(title: str) -> str:
    """중복 판정용 제목 정규화 — Q4 플랫폼의 'Download, <제목>, <날짜>,
    (opens in new window)' PDF 링크를 본문 링크와 같은 항목으로 인식."""
    t = title.lower()
    t = re.sub(r"^download,?\s*", "", t)
    t = re.sub(r"\(opens in new window\)", "", t)
    t = re.sub(
        r"(january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\s+\d{1,2},?\s+20\d{2}", "", t)
    return re.sub(r"[^a-z0-9가-힣]+", " ", t).strip()


def _company_tokens(company: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]{3,}", (company or "").lower())
            if t not in _NAME_STOPWORDS]


def score_candidate(url: str, company: str, ticker: str) -> int:
    """IR 뉴스 목록 페이지 후보 점수 — 회사 자체 도메인의 news/press 경로 우대."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return -10
    host, path = parsed.netloc.lower(), parsed.path.lower()
    score = 0
    if re.search(r"news|press|release|media", path):
        score += 2
    if "invest" in host + path or host.startswith("ir."):
        score += 1
    tokens = _company_tokens(company)
    if (ticker or "").lower() in host.replace("-", "") or any(
            t in host for t in tokens):
        score += 3
    if any(h in host for h in _AGGREGATOR_HOSTS):
        score -= 3
    if path in ("", "/"):
        score -= 1
    return score


def build_news_chronicle(
    company: str, ticker: str, listing_url: str, items: list[dict], years: int,
) -> str:
    rng = (f"{items[0]['date']} ~ {items[-1]['date']}" if items else "없음")
    lines = [
        f"【IR 뉴스룸 연대기 — {company} ({ticker})】",
        f"목록 페이지: {listing_url}",
        f"회사 보도자료 시간순 총 {len(items)}건, 실제 수집 범위: {rng} "
        "(목록 첫 페이지 기준 — 이보다 오래된 사건은 SEC 공시 연대기 참조). "
        "8-K 의무공시가 아닌 제품 출시·파트너십·수주 발표 포함.",
        "",
    ]
    for it in items:
        lines.append(f"{it['date']} | {it['title']}")
        lines.append(f"  원문: {it['url']}")
    return "\n".join(lines)


# ── 소스 본체 ──────────────────────────────────────────────────────

class IRNewsroomSource(BaseSource):
    """티커 → IR 뉴스룸 연대기 + 최신 PR 추출 대상. 검색은 목록 탐색 1회만."""

    source_type = "ir_newsroom"

    def __init__(self):
        self._search_sources: list[BaseSource] = []
        self._jina = JinaReaderSource()

    def set_sources(self, *sources) -> None:
        self._search_sources = [s for s in sources if s is not None]

    def is_available(self) -> bool:
        return bool(self._search_sources)

    async def search(self, query: str, **kwargs) -> list[SearchResult]:
        return []  # collect()가 진입점

    async def collect(
        self, ticker: str, company: Optional[str] = None,
        years: int = 3, max_items: int = 10,
    ) -> tuple[Optional[ExtractedContent], list[SearchResult]]:
        """(뉴스 연대기 문서, 최신 PR 추출 대상들). 실패 시 (None, [])."""
        ticker = (ticker or "").strip().upper()
        if not ticker:
            return None, []
        try:
            if not company:
                company = await self._company_from_sec(ticker)
            company = company or ticker

            candidates = await self._find_listing_candidates(company, ticker)
            if not candidates:
                logger.info(f"[ir_newsroom] {ticker} 목록 페이지 후보 없음")
                return None, []

            for url in candidates[:3]:
                page = await self._jina.extract(url)
                if not page:
                    continue
                items = parse_news_items(page.content, years)
                if len(items) < 3:
                    items = await self._llm_parse(page.content, years)
                if len(items) < 3:
                    logger.info(f"[ir_newsroom] 항목 부족({len(items)}건): {url}")
                    continue

                chronicle = ExtractedContent(
                    url=url,
                    title=f"[IR 뉴스룸 연대기] {company} ({ticker}) 최근 {years}년",
                    content=build_news_chronicle(
                        company, ticker, url, items, years),
                    domain=urlparse(url).netloc,
                )
                chronicle.word_count = len(chronicle.content.split())

                latest = items[-max_items:]
                targets = [SearchResult(
                    url=it["url"],
                    title=it["title"],
                    content=f"IR 보도자료 ({it['date']})",
                    source_type=self.source_type,
                    relevance_score=0.9,  # 회사 1차 자료 — 추출 우선권
                    published_date=it["date"],
                ) for it in reversed(latest)]  # 최신부터 추출
                logger.info(
                    f"[ir_newsroom] {ticker}: {url} → 항목 {len(items)}건, "
                    f"추출 대상 {len(targets)}건")
                return chronicle, targets

            logger.info(f"[ir_newsroom] {ticker} 유효한 목록 페이지 못 찾음")
            return None, []
        except Exception as e:
            logger.warning(f"[ir_newsroom] {ticker} 수집 예외: {e}")
            return None, []

    async def _company_from_sec(self, ticker: str) -> Optional[str]:
        try:
            async with self._make_client() as client:
                pair = await resolve_ticker(self, client, _SEC_HEADERS, ticker)
                return pair[1] if pair else None
        except Exception:
            return None

    async def _find_listing_candidates(
        self, company: str, ticker: str,
    ) -> list[str]:
        """검색 1회로 후보 URL 수집 → 점수순. 첫 응답 소스만 사용(쿼터 절약)."""
        query = f'"{company}" investor relations news press releases'
        results: list[SearchResult] = []
        for src in self._search_sources:
            if not src.is_available():
                continue
            try:
                results = await src.search(query, num_results=8)
            except Exception as e:
                logger.debug(f"[ir_newsroom] 후보 검색 실패({src.source_type}): {e}")
                results = []
            if results:
                break
        urls: list[str] = []
        seen: set[str] = set()
        for r in results:
            if r.url and r.url not in seen:
                seen.add(r.url)
                urls.append(r.url)
        urls.sort(key=lambda u: score_candidate(u, company, ticker),
                  reverse=True)
        return urls

    async def _llm_parse(self, markdown: str, years: int) -> list[dict]:
        """결정론 파싱 실패 시에만 — lite 모델로 JSON 추출 (저비용 폴백)."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=365 * years)).strftime("%Y-%m-%d")
        prompt = (
            "다음은 기업 IR 뉴스 목록 페이지의 텍스트다. 보도자료 항목만 "
            "JSON 배열로 추출하라. 각 항목: {\"date\": \"YYYY-MM-DD\", "
            "\"title\": \"...\", \"url\": \"https://...\"}. 날짜나 URL이 "
            "없는 항목·내비게이션 링크는 제외. JSON 배열만 출력.\n\n"
            f"{markdown[:15000]}"
        )
        text = await llm_client.generate_text(
            prompt, GEMINI_LITE_MODEL, timeout_s=60, tag="ir_newsroom")
        if not text:
            return []
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                      flags=re.MULTILINE).strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
        items = []
        for it in raw if isinstance(raw, list) else []:
            date = str(it.get("date", ""))[:10]
            url = str(it.get("url", ""))
            title = str(it.get("title", "")).strip()
            if (re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date)
                    and url.startswith("http") and len(title) >= 10
                    and date >= cutoff):
                items.append({"date": date, "title": title, "url": url})
        items.sort(key=lambda it: it["date"])
        return items
