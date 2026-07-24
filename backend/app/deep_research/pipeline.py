from __future__ import annotations
import asyncio
import logging
import re
import time
import uuid
from typing import AsyncGenerator, Optional

from app.deep_research.agents.planner import Planner
from app.deep_research.agents.searcher import Searcher
from app.deep_research.agents.extractor import Extractor
from app.deep_research.agents.critic import Critic
from app.deep_research.agents.synthesizer import Synthesizer
from app.deep_research import llm_client
from app.deep_research.agents.jurisdiction_detector import jurisdiction_detector
from app.deep_research.agents.evidence_ranker import evidence_ranker
from app.deep_research.sources.official_source_searcher import official_source_searcher
from app.deep_research.sources.filing_timeline import FilingTimelineSource
from app.deep_research.sources.ir_newsroom import IRNewsroomSource
from app.deep_research.sources.market_context import MarketContextSource
from app.deep_research.storage.raw_sources import RawSourceStorage
from app.deep_research.discovery.lead_follower import lead_follower
from app.deep_research.discovery.alternate_finder import alternate_instance_finder
from app.deep_research.discovery.accessible_resolver import accessible_resolver, is_gated
from app.deep_research.common import domain_of
from app.deep_research.config import (
    MAX_ITERATIONS, MAX_RUN_SECONDS, MAX_COST_USD_PER_RUN,
    DISCOVERY_ENABLED, DISCOVERY_MAX_DEPTH, DISCOVERY_BREADTH, DISCOVERY_MAX_SEARCHES,
)
from app.deep_research.models import (
    DeepResearchRequest, DeepResearchResponse,
    JobStatus, JobStatusResponse, ProgressEvent, ResearchMetadata,
    CoverageInfo, SubQuery, SearchResult,
)

logger = logging.getLogger(__name__)

# 진행 중인 작업 저장소 (메모리, 프로세스 재시작 시 초기화)
_jobs: dict[str, JobStatusResponse] = {}
_job_queues: dict[str, asyncio.Queue] = {}

_INSIDER_KW = frozenset([
    "insider", "executive", "officer", "director", "form 4",
    "stock sale", "shares sold", "insider trading", "c-level",
    "임원", "내부자", "주식 매도", "지분 변동", "베스팅", "rsu",
    "insider transaction", "insider purchase", "ownership change",
])

# 기업 자산/지분 매각 (M&A/divestiture) — Form 4가 아닌 8-K 대상
_DIVESTITURE_KW = frozenset([
    "지분 매각", "자산 매각", "stake sale", "divest", "divestiture",
    "asset sale", "asset divestiture", "spin-off", "carve-out",
    "disposition", "sells stake", "sold stake",
])

def _kw_match(query: str, keywords: frozenset) -> bool:
    """단어경계 키워드 매칭. 부분문자열 매칭은 'rsu' in 'pursue' 같은 오탐으로
    무관한 질의를 Form 4/8-K 경로에 태운다. ASCII 키워드는 \\b 경계,
    CJK 키워드(경계 개념 없음)는 기존처럼 부분 매치."""
    q = query.lower()
    for kw in keywords:
        if kw.isascii():
            if re.search(rf"\b{re.escape(kw)}\b", q):
                return True
        elif kw in q:
            return True
    return False


def _is_insider_query(query: str) -> bool:
    return _kw_match(query, _INSIDER_KW)

def _is_divestiture_query(query: str) -> bool:
    return _kw_match(query, _DIVESTITURE_KW)


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = (item or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _build_counter_queries(request: DeepResearchRequest) -> list[SubQuery]:
    """질문 종류·산업과 무관하게 최소 반증/공식 fallback 쿼리를 만든다."""
    ctx = request.context or {}
    anchor = " ".join(filter(None, [
        str(ctx.get("ticker") or "").strip(),
        str(ctx.get("company_name") or ctx.get("company") or "").strip(),
    ])).strip()
    counter_base = f"{anchor} {request.query}".strip()
    return [
        SubQuery(
            query=(
                f"{counter_base} contradictory evidence downside risks "
                f"failure conditions latest"
            ),
            priority=1,
            sources=["grounding", "tavily", "parallel"],
            rationale="counter_evidence: 투자 논리를 반박하거나 무효화할 최신 근거",
        ),
        SubQuery(
            query=(
                f"{counter_base} site:sec.gov 10-K 10-Q risk factors "
                f"legal proceedings commitments contingencies"
            ),
            priority=1,
            sources=["sec", "grounding", "tavily", "parallel"],
            rationale="counter_evidence: 공식 공시 fallback과 누락 위험 확인",
        ),
    ]


class DeepResearchPipeline:
    """심층 리서치 메인 파이프라인."""

    def __init__(self):
        self.planner = Planner()
        self.searcher = Searcher()
        self.extractor = Extractor()
        self.critic = Critic()
        self.synthesizer = Synthesizer()
        self.filing_timeline = FilingTimelineSource()
        self.ir_newsroom = IRNewsroomSource()
        self.market_context = MarketContextSource()
        self._official_searcher_initialized = False
        self._discovery_wired = False
        # 하위 에이전트와 llm_client 사용량 계측이 mutable state를 보유한다.
        # 동일 프로세스의 동시 실행이 서로 reset하지 못하도록 정확성을 우선해 직렬화한다.
        self._run_lock = asyncio.Lock()

    async def run(
        self,
        request: DeepResearchRequest,
        job_id: str,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> DeepResearchResponse:
        async with self._run_lock:
            return await self._run_isolated(request, job_id, event_queue)

    async def _run_isolated(
        self,
        request: DeepResearchRequest,
        job_id: str,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> DeepResearchResponse:
        start_time = time.time()
        metadata = ResearchMetadata()

        _last_pct = 0

        async def emit(stage: str, message: str, pct: int, data: Optional[dict] = None):
            # 진행률 단조 증가 보장 — 단계별 하드코딩 pct가 실행 순서와 어긋나면
            # (예: 추출완료 50 → 공식소스 32) 프론트 진행바가 역행한다.
            nonlocal _last_pct
            pct = max(pct, _last_pct)
            _last_pct = pct
            logger.info(f"[pipeline][{stage}] {message} ({pct}%)")
            if event_queue:
                event = ProgressEvent(
                    job_id=job_id, stage=stage, message=message,
                    progress_pct=pct, data=data,
                )
                await event_queue.put(event)
                _update_job_status(job_id, JobStatus.RUNNING, pct, stage, message)

        try:
            raw_storage = RawSourceStorage()

            # ── 0. 관할 감지 + 공식 소스 검색기 초기화 ──
            jurisdiction = jurisdiction_detector.detect(request.query, request.context)
            logger.info(
                f"[pipeline] 관할={jurisdiction.primary} "
                f"cross={jurisdiction.is_cross_border} "
                f"secondary={jurisdiction.secondary[:2]}"
            )
            if not self._official_searcher_initialized:
                tavily_src = self.searcher._sources.get("tavily")
                parallel_src = self.searcher._sources.get("parallel")
                official_source_searcher.set_sources(tavily_src, parallel_src)
                self.ir_newsroom.set_sources(
                    tavily_src, self.searcher._sources.get("grounding"),
                    parallel_src,
                )
                self._official_searcher_initialized = True

            official_source_searcher.reset_tracking()
            llm_client.reset_usage()  # 잡 단위 실비용 집계 시작

            # ── 잡 간 상태 누수 방지 (싱글턴 파이프라인) ──
            # searcher._url_seen/_total_queries·extractor._extracted_urls가 리셋되지
            # 않으면 두 번째 리서치부터 이전 잡의 URL이 영구 스킵되고, 쿼리 상한이
            # 프로세스 수명 총량이 되어 검색이 조용히 빈 결과를 반환한다.
            self.searcher.reset()
            self.extractor.reset()
            self.planner.reset_usage()
            self.critic.reset_usage()
            self.synthesizer.reset_usage()

            # Discovery 엔진 배선 (대체 인스턴스 검색 + n차 단서추적)
            if not self._discovery_wired:
                alternate_instance_finder.set_sources(
                    self.searcher._sources.get("tavily"),
                    self.searcher._sources.get("parallel"),
                )
                lead_follower.set_search(self._discovery_search)
                self._discovery_wired = True

            # ── 1. 계획 수립 ──
            await emit("planning", "질의 분석 및 리서치 계획 수립 중...", 5)
            plan = await self.planner.plan(request.query, request.context)
            metadata.generated_queries = _dedupe_strings([q.query for q in plan.sub_queries])
            metadata.official_source_queries = _dedupe_strings([
                q for q in metadata.generated_queries if "site:" in q.lower()
            ])
            await emit("planning", f"{len(plan.sub_queries)}개 검색 쿼리 생성 완료", 10,
                      {"sub_queries": len(plan.sub_queries)})

            all_results = []
            all_contents = []

            # ── 2. 초기 검색 (우선순위 1,2) ──
            await emit("searching", "1차 병렬 검색 시작...", 15)
            results = await self.searcher.search_plan(plan, priority_filter=2)
            all_results.extend(results)
            await emit("searching", f"{len(results)}개 결과 수집", 30,
                      {"results_count": len(results)})

            # ── 3. 초기 전문 추출 (+페이월 복구) ──
            # 메인 검색의 WSJ/FT/Bloomberg 결과도 아카이브/대체 인스턴스로 복구를
            # 시도한다 (이전: Discovery 경로에서만 복구 → 메인 결과는 그냥 소멸).
            await emit("extracting", "웹 페이지 전문 추출 중...", 35)
            max_extract = request.max_sources or 30
            contents, recovered_main = await self._extract_with_recovery(
                results, max_extract=max_extract,
            )
            metadata.recovered_sources += recovered_main
            all_contents.extend(contents)
            for c in contents:
                raw_storage.store(c.url, c.title, c.content, domain_of(c.url))
            await emit("extracting", f"{len(contents)}개 페이지 추출 완료", 50,
                      {"extracted_count": len(contents)})

            # ── 3a. 공식 소스 집중 검색 (전 관할 — US 단일 포함) ──
            # 과거엔 US 단일 관할이면 스킵했으나, 그러면 가장 흔한 케이스(미국 종목)에서
            # SEC EDGAR·IR 1차 자료 직접 타격이 빠진다(INDI 라이브 실측: 공식쿼리 0).
            official_results_count = 0
            official_extracted_count = 0
            sec_label = jurisdiction.primary + (
                f"+{jurisdiction.secondary[0]}" if jurisdiction.secondary else ""
            )
            await emit("searching", f"공식 소스 집중 검색 ({sec_label})...", 51)
            try:
                official_results = await official_source_searcher.search(
                    request.query, jurisdiction,
                    max_results_per_query=5,
                    context=request.context,
                )
                if official_results:
                    all_results.extend(official_results)
                    official_results_count = len(official_results)
                    # 본문 추출
                    official_contents = await self.extractor.extract_from_results(
                        official_results,
                        max_extract=min(len(official_results), 10),
                    )
                    all_contents.extend(official_contents)
                    for c in official_contents:
                        raw_storage.store(
                            c.url, c.title, c.content, domain_of(c.url),
                        )
                    official_extracted_count = len(official_contents)
                    await emit(
                        "searching",
                        f"공식 소스 {official_results_count}건 수집 / "
                        f"{official_extracted_count}건 본문 추출",
                        53,
                    )
            except Exception as e:
                logger.warning(f"[pipeline] 공식 소스 검색 실패 (계속): {e}")

            metadata.generated_queries = _dedupe_strings(
                metadata.generated_queries + official_source_searcher.last_query_strings
            )
            metadata.official_source_queries = _dedupe_strings(
                metadata.official_source_queries + official_source_searcher.last_query_strings
            )
            metadata.searched_official_domains = official_source_searcher.last_searched_domains

            # ── 3b. SEC Form 4 직접 파싱 (임원 거래 관련 쿼리) ──
            if _is_insider_query(request.query):
                ticker = (request.context or {}).get("ticker", "")
                if ticker:
                    await emit("searching", f"SEC Form 4 직접 조회 중 ({ticker})...", 52)
                    try:
                        sec_src = self.searcher._sources.get("sec")
                        if sec_src:
                            form4_contents = await sec_src.fetch_insider_trades(ticker, limit=5)
                            if form4_contents:
                                all_contents.extend(form4_contents)
                                for c in form4_contents:
                                    raw_storage.store(c.url, c.title, c.content, "sec.gov")
                                await emit("searching",
                                          f"Form 4 원본 {len(form4_contents)}건 파싱 완료", 54,
                                          {"form4_count": len(form4_contents)})
                    except Exception as e:
                        logger.warning(f"[pipeline] Form 4 조회 실패 (계속 진행): {e}")

            # ── 3c. SEC 8-K 직접 검색 (지분/자산 매각 관련 쿼리) ──
            if _is_divestiture_query(request.query):
                ticker = (request.context or {}).get("ticker", "")
                if ticker:
                    await emit("searching", f"SEC 8-K 공시 직접 조회 중 ({ticker})...", 55)
                    try:
                        sec_src = self.searcher._sources.get("sec")
                        if sec_src:
                            eight_k_results = await sec_src.search(
                                f"{ticker} asset sale divestiture stake",
                                forms="8-K,6-K",
                                num_results=8,
                            )
                            if eight_k_results:
                                all_results.extend(eight_k_results)
                                extra_contents = await self.extractor.extract_from_results(
                                    eight_k_results, max_extract=5
                                )
                                all_contents.extend(extra_contents)
                                for c in extra_contents:
                                    raw_storage.store(c.url, c.title, c.content, "sec.gov")
                                await emit("searching",
                                          f"SEC 8-K {len(eight_k_results)}건 수집 완료", 57,
                                          {"eight_k_count": len(eight_k_results)})
                    except Exception as e:
                        logger.warning(f"[pipeline] SEC 8-K 조회 실패 (계속 진행): {e}")

            # ── 3d. 공시 연대기 수집 (티커 기반 상시 — 쿼리 무관) ──
            # 검색 기반 수집은 '검색에 걸린 사건'만 가져온다. 실적 PR·전환사채·
            # 지분매각 같은 8-K 사건이 통째로 빠지는 격차의 해소 — EDGAR 제출
            # 이력 자체를 시간축으로 전수 수집 (INDI 비교 감사 실측 기반).
            tl_ticker = (request.context or {}).get("ticker", "")
            if tl_ticker:
                await emit("searching", f"SEC 공시 연대기 수집 중 ({tl_ticker})...", 57)
                try:
                    tl_contents = await self.filing_timeline.collect(tl_ticker)
                    if tl_contents:
                        all_contents.extend(tl_contents)
                        for c in tl_contents:
                            raw_storage.store(c.url, c.title, c.content, c.domain)
                        await emit("searching",
                                  f"공시 연대기 확보 + 중요 8-K 원문 {len(tl_contents) - 1}건",
                                  58, {"filing_timeline_docs": len(tl_contents)})
                except Exception as e:
                    logger.warning(f"[pipeline] 공시 연대기 수집 실패 (계속): {e}")

                # ── 3d-2. IR 뉴스룸 연대기 (8-K 비의무 PR — 제품/파트너십) ──
                try:
                    ctx = request.context or {}
                    news_chron, pr_targets = await self.ir_newsroom.collect(
                        tl_ticker,
                        company=ctx.get("company_name") or ctx.get("company"),
                    )
                    if news_chron:
                        all_contents.append(news_chron)
                        raw_storage.store(news_chron.url, news_chron.title,
                                          news_chron.content, news_chron.domain)
                    if pr_targets:
                        all_results.extend(pr_targets)
                        pr_contents = await self.extractor.extract_from_results(
                            pr_targets, max_extract=10)
                        all_contents.extend(pr_contents)
                        for c in pr_contents:
                            raw_storage.store(c.url, c.title, c.content,
                                              domain_of(c.url))
                        await emit("searching",
                                  f"IR 뉴스룸 연대기 확보 + 보도자료 원문 "
                                  f"{len(pr_contents)}건", 58,
                                  {"ir_newsroom_docs": len(pr_contents)})
                except Exception as e:
                    logger.warning(f"[pipeline] IR 뉴스룸 수집 실패 (계속): {e}")

                # ── 3d-3. 시장 수급 스냅샷 (공매도·목표주가·추천 — LLM 0콜) ──
                try:
                    mkt = await self.market_context.collect(tl_ticker)
                    if mkt:
                        all_contents.append(mkt)
                        raw_storage.store(mkt.url, mkt.title, mkt.content,
                                          mkt.domain)
                        await emit("searching",
                                  "시장 수급 스냅샷 확보 (공매도·목표주가·추천)",
                                  58, {"market_context": True})
                except Exception as e:
                    logger.warning(f"[pipeline] 시장 수급 수집 실패 (계속): {e}")

            # ── 3e. Discovery 심층 확장 (n차 단서추적 + 접근가능본 복구) ──
            if DISCOVERY_ENABLED:
                await emit("searching", "심층 단서추적 (n차 확장)...", 58)
                try:
                    lead_result = await lead_follower.deepen(
                        request.query,
                        max_depth=DISCOVERY_MAX_DEPTH,
                        breadth=DISCOVERY_BREADTH,
                        max_searches=DISCOVERY_MAX_SEARCHES,
                    )
                    metadata.discovery_leads = len(lead_result.explored)
                    if lead_result.sources:
                        all_results.extend(lead_result.sources)
                        disc_contents, recovered = await self._extract_with_recovery(
                            lead_result.sources, max_extract=15,
                        )
                        metadata.recovered_sources += recovered  # 메인 경로 복구분에 누적
                        all_contents.extend(disc_contents)
                        for c in disc_contents:
                            raw_storage.store(c.url, c.title, c.content, domain_of(c.url))
                        await emit("searching",
                                   f"심층 확장: 단서 {metadata.discovery_leads}개 추적, "
                                   f"본문 {len(disc_contents)}건(+복구 {recovered}) 추가", 59)
                except Exception as e:
                    logger.warning(f"[pipeline] Discovery 심층 확장 실패 (계속): {e}")

            # ── 3f. 결정론적 반증 검색 + 공식 공시 fallback ──
            # Planner가 반증 쿼리를 누락하거나 LLM 폴백 계획을 사용해도 최소 2개는
            # 항상 실행한다. 기업/산업별 키워드를 하드코딩하지 않고 원 질문과
            # 티커를 앵커로 사용한다.
            counter_queries = _build_counter_queries(request)
            metadata.counter_evidence_queries = [q.query for q in counter_queries]
            metadata.generated_queries = _dedupe_strings(
                metadata.generated_queries + metadata.counter_evidence_queries
            )
            await emit("searching", "반대 근거·공식 공시 fallback 검색...", 59)
            try:
                counter_results = await self.searcher.search_queries(counter_queries)
                if counter_results:
                    all_results.extend(counter_results)
                    counter_contents, counter_recovered = await self._extract_with_recovery(
                        counter_results, max_extract=12, max_recovery=3,
                    )
                    metadata.recovered_sources += counter_recovered
                    all_contents.extend(counter_contents)
                    for c in counter_contents:
                        raw_storage.store(c.url, c.title, c.content, domain_of(c.url))
                    await emit(
                        "searching",
                        f"반대 근거 {len(counter_contents)}개 본문 확보",
                        60,
                        {"counter_evidence_docs": len(counter_contents)},
                    )
            except Exception as e:
                logger.warning(f"[pipeline] 반증/fallback 검색 실패 (계속): {e}")

            # ── 4. 반사 루프 ──
            max_iter = request.max_iterations or MAX_ITERATIONS
            # 초기 검색에서 사용된 쿼리 추적 (priority ≤ 2)
            searched_queries: set[str] = {
                q.query for q in plan.sub_queries if q.priority <= 2
            }

            for iteration in range(1, max_iter + 1):
                elapsed = time.time() - start_time
                if elapsed > MAX_RUN_SECONDS:
                    logger.warning(f"[pipeline] 시간 초과: {elapsed:.0f}s")
                    break
                # 비용 가드 — llm_client 전수 집계 기반(입력+출력+사고, 현행 단가)
                run_cost = llm_client.estimated_cost_usd()
                if run_cost > MAX_COST_USD_PER_RUN:
                    logger.warning(f"[pipeline] 비용 초과: ${run_cost:.3f} > ${MAX_COST_USD_PER_RUN}")
                    break

                await emit("reflecting", f"정보 충분성 평가 (라운드 {iteration})...",
                          60 + iteration * 4)
                gap = await self.critic.evaluate(plan, all_contents, iteration)
                metadata.iterations = iteration

                if gap.is_sufficient:
                    await emit("reflecting",
                              f"정보 충분 (신뢰도: {gap.confidence:.0%})", 70)
                    break

                queries_to_run = list(gap.additional_queries)
                if not queries_to_run:
                    # critic이 is_sufficient=False이지만 추가 쿼리를 생성 못한 경우
                    # plan의 미사용 서브쿼리(priority 3)로 보완
                    unused = [q for q in plan.sub_queries
                              if q.query not in searched_queries]
                    if not unused:
                        logger.info("[pipeline] 추가 쿼리 없고 잔여 서브쿼리도 없음 → 루프 종료")
                        break
                    queries_to_run = unused[:3]
                    logger.info(
                        f"[pipeline] critic 추가 쿼리 없음 → "
                        f"plan 잔여 쿼리 {len(queries_to_run)}개 활용"
                    )

                await emit("searching",
                          f"보완 검색: {len(queries_to_run)}개 쿼리", 70)
                extra_results = await self.searcher.search_queries(queries_to_run)
                searched_queries.update(q.query for q in queries_to_run)
                all_results.extend(extra_results)

                extra_contents, recovered_extra = await self._extract_with_recovery(
                    extra_results, max_extract=10, max_recovery=3,
                )
                metadata.recovered_sources += recovered_extra
                all_contents.extend(extra_contents)
                for c in extra_contents:
                    raw_storage.store(c.url, c.title, c.content, domain_of(c.url))
                await emit("extracting",
                          f"추가 {len(extra_contents)}개 페이지 추출", 75)

            # ── 5. 최종 보고서 생성 ──
            await emit("synthesizing", "최종 보고서 작성 중 (Gemini)...", 80)

            elapsed = time.time() - start_time
            metadata.total_queries = self.searcher.total_queries + official_source_searcher.last_query_count
            metadata.elapsed_seconds = elapsed

            # URL 기준 명시적 dedup — 단계별(초기+공식+8-K+discovery+반사루프) extend로
            # 같은 URL이 두 번 들어오면 synthesizer/numeric_consistency의
            # '출처 N곳' 카운트가 부풀어 프레이밍 상충 오탐의 원인이 된다.
            _seen_urls: set[str] = set()
            _deduped = []
            for c in all_contents:
                if c.url and c.url in _seen_urls:
                    continue
                if c.url:
                    _seen_urls.add(c.url)
                _deduped.append(c)
            all_contents = _deduped

            # 신뢰도 기준 콘텐츠 정렬 (상위 출처 우선 노출)
            all_contents = evidence_ranker.rank_contents(all_contents)

            # coverage 정보 생성 (티커가 있으면 종목 질문 — 기대 도메인을
            # 증권 규제기관·거래소로 한정, 거시 기관은 기대치에서 제외.
            # 컨텍스트 티커 외에 쿼리에서 감지된 티커 시그널도 인정 — 제너릭 실행 대응)
            collected_urls = [c.url for c in all_contents]
            has_ticker_signal = bool((request.context or {}).get("ticker")) or any(
                s.startswith(("ticker:", "ctx_ticker:"))
                for sigs in jurisdiction.signals.values() for s in sigs
            )
            coverage_topic = "company" if has_ticker_signal else "all"
            coverage_dict = official_source_searcher.build_coverage_info(
                jurisdiction, collected_urls,
                official_extracted_count=official_extracted_count,
                topic=coverage_topic,
            )
            coverage = CoverageInfo(
                checked=coverage_dict["checked"],
                unchecked=coverage_dict["unchecked"],
                notes=coverage_dict["notes"],
            )

            await emit("synthesizing", f"환각 검증 준비 ({len(raw_storage)}개 원본 저장됨)...", 82)

            async def _emit_draft(draft_report):
                # ② 초안 즉시 표시 — 심사(방어선 5+) 전 판본을 프론트로 먼저.
                await emit("draft", "초안 완성 — 심사 중 (검증·교차확인 진행)...", 88,
                           {"draft_report": draft_report.model_dump(mode="json")})

            report = await self.synthesizer.synthesize(
                query=request.query,
                contents=all_contents,
                search_results=all_results,
                required_sections=plan.required_sections,
                metadata=metadata,
                job_id=job_id,
                raw_storage=raw_storage,
                coverage=coverage,
                context=request.context,  # XBRL 원장 대조용 ticker 전달
                on_draft=_emit_draft,
            )

            report.metadata.elapsed_seconds = time.time() - start_time
            # 실비용: llm_client 전수 집계(모델별 입력+출력+사고 × 현행 단가).
            # 레거시 폴백 경로는 집계 밖이라 하한값 — 로그에 모델별 내역 남김.
            report.metadata.estimated_cost_usd = llm_client.estimated_cost_usd()
            report.metadata.gemini_tokens_used = llm_client.total_tokens()
            logger.info(f"[pipeline] LLM 사용량: {llm_client.get_usage()} "
                        f"→ ${report.metadata.estimated_cost_usd:.4f}")
            report.metadata.generated_queries = metadata.generated_queries
            report.metadata.official_source_queries = metadata.official_source_queries
            report.metadata.searched_official_domains = metadata.searched_official_domains
            report.metadata.counter_evidence_queries = metadata.counter_evidence_queries
            report.generated_queries = report.metadata.generated_queries
            report.official_source_queries = report.metadata.official_source_queries
            report.search_attempts = self.searcher.attempts


            await emit("done", "리서치 완료!", 100, {"job_id": job_id})
            _update_job_status(job_id, JobStatus.DONE, 100, "done", "완료",
                              result=report)

            logger.info(
                f"[pipeline] 완료: {report.metadata.elapsed_seconds:.1f}s, "
                f"쿼리={report.metadata.total_queries}, "
                f"출처={report.metadata.total_sources}, "
                f"비용=${report.metadata.estimated_cost_usd:.3f}"
            )
            return report

        except Exception as e:
            logger.error(f"[pipeline] 치명적 오류: {e}", exc_info=True)
            error_msg = f"리서치 실패: {str(e)}"
            _update_job_status(job_id, JobStatus.FAILED, 0, "error", error_msg)
            if event_queue:
                await event_queue.put(ProgressEvent(
                    job_id=job_id, stage="error", message=error_msg, progress_pct=0
                ))
            raise

    async def _discovery_search(self, query: str) -> list[SearchResult]:
        """lead_follower용 검색 어댑터 — 기존 searcher(다중소스·URL 중복제거)를 재사용."""
        return await self.searcher.search_queries(
            [SubQuery(query=query, priority=1, sources=["tavily", "parallel"])]
        )

    async def _extract_with_recovery(
        self, results: list[SearchResult], max_extract: int = 15,
        max_recovery: int = 5,
    ) -> tuple[list, int]:
        """본문 추출 + 게이트/실패 출처를 접근가능본으로 복구.

        1) 정상 추출 (extractor가 페이월 도메인은 시도조차 안 하고 거른다)
        2) 걸러졌거나 실패한 게이트 URL → accessible_resolver(아카이브 스냅샷) 우선,
           없으면 alternate_finder(타 호스트 인스턴스)로 접근가능본을 찾아 재추출
        반환: (추출 콘텐츠, 복구로 확보한 출처 수)

        메인 검색 경로에서도 호출된다 — WSJ/FT/Bloomberg 결과가 복구 기회 없이
        소멸하던 문제의 배선 수정. max_recovery로 Wayback 순차조회 지연을 제한.
        """
        contents = await self.extractor.extract_from_results(results, max_extract=max_extract)
        got = {c.url for c in contents}

        recovery_targets: list[SearchResult] = []
        seen_gated: set[str] = set()
        attempts = 0
        for r in results:
            if attempts >= max_recovery:
                break
            if not r.url or r.url in got or r.url in seen_gated or not is_gated(r.url):
                continue
            seen_gated.add(r.url)
            attempts += 1
            alt = await accessible_resolver.find_accessible_url(r.url)
            if alt:
                recovery_targets.append(SearchResult(
                    url=alt.accessible_url, title=r.title, content=r.content,
                    source_type="wayback", relevance_score=r.relevance_score,
                ))
            elif r.title:
                recovery_targets.extend(
                    await alternate_instance_finder.find_alternates(
                        r.title, exclude_url=r.url, limit=1,
                    )
                )

        recovered = 0
        if recovery_targets:
            rec = await self.extractor.extract_from_results(
                recovery_targets, max_extract=len(recovery_targets),
            )
            contents.extend(rec)
            recovered = len(rec)
        return contents, recovered


# ── 작업 관리 ──

def create_job(job_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _cleanup_finished_jobs()  # 새 잡 생성 시 오래된 완료 잡 lazy 정리
    _jobs[job_id] = JobStatusResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        progress_pct=0,
        current_stage="pending",
        message="대기 중...",
    )
    _job_queues[job_id] = queue
    return queue


# 완료/실패 잡 보존 시간 — 삭제 코드가 없으면 리포트 전문이 프로세스 메모리에
# 무한 축적된다. 완료 후 TTL 동안은 status 조회 가능, 이후 lazy 정리.
_JOB_TTL_SECONDS = 3600
_job_finished_at: dict[str, float] = {}


def _cleanup_finished_jobs() -> None:
    now = time.time()
    expired = [jid for jid, ts in _job_finished_at.items()
               if now - ts > _JOB_TTL_SECONDS]
    for jid in expired:
        _jobs.pop(jid, None)
        _job_queues.pop(jid, None)
        _job_finished_at.pop(jid, None)
    if expired:
        logger.info(f"[pipeline] 완료 잡 {len(expired)}건 정리 (TTL {_JOB_TTL_SECONDS}s)")


def get_job_status(job_id: str) -> Optional[JobStatusResponse]:
    return _jobs.get(job_id)


def _update_job_status(
    job_id: str,
    status: JobStatus,
    pct: int,
    stage: str,
    message: str,
    result: Optional[DeepResearchResponse] = None,
):
    if job_id in _jobs:
        _jobs[job_id].status = status
        _jobs[job_id].progress_pct = pct
        _jobs[job_id].current_stage = stage
        _jobs[job_id].message = message
        if result:
            _jobs[job_id].result = result
        if status in (JobStatus.DONE, JobStatus.FAILED):
            _job_finished_at[job_id] = time.time()


async def stream_events(job_id: str) -> AsyncGenerator[str, None]:
    """SSE 스트림 — 작업 이벤트를 text/event-stream 형식으로 전달."""
    queue = _job_queues.get(job_id)
    if queue is None:
        yield "data: {\"error\": \"job not found\"}\n\n"
        return

    while True:
        try:
            event: ProgressEvent = await asyncio.wait_for(queue.get(), timeout=30.0)
            yield f"data: {event.model_dump_json()}\n\n"
            if event.stage in ("done", "error"):
                break
        except asyncio.TimeoutError:
            yield "data: {\"stage\": \"heartbeat\"}\n\n"
        except Exception as e:
            logger.error(f"[pipeline] SSE 스트림 오류: {e}")
            break
