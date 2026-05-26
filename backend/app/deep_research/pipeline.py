from __future__ import annotations
import asyncio
import logging
import time
import uuid
from typing import AsyncGenerator, Optional

from app.deep_research.agents.planner import Planner
from app.deep_research.agents.searcher import Searcher
from app.deep_research.agents.extractor import Extractor
from app.deep_research.agents.critic import Critic
from app.deep_research.agents.synthesizer import Synthesizer
from app.deep_research.agents.jurisdiction_detector import jurisdiction_detector
from app.deep_research.agents.evidence_ranker import evidence_ranker
from app.deep_research.sources.official_source_searcher import official_source_searcher
from app.deep_research.storage.raw_sources import RawSourceStorage
from app.deep_research.config import MAX_ITERATIONS, MAX_RUN_SECONDS, MAX_COST_USD_PER_RUN
from app.deep_research.models import (
    DeepResearchRequest, DeepResearchResponse,
    JobStatus, JobStatusResponse, ProgressEvent, ResearchMetadata,
    CoverageInfo,
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

def _is_insider_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _INSIDER_KW)

def _is_divestiture_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _DIVESTITURE_KW)


class DeepResearchPipeline:
    """심층 리서치 메인 파이프라인."""

    def __init__(self):
        self.planner = Planner()
        self.searcher = Searcher()
        self.extractor = Extractor()
        self.critic = Critic()
        self.synthesizer = Synthesizer()
        self._official_searcher_initialized = False

    async def run(
        self,
        request: DeepResearchRequest,
        job_id: str,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> DeepResearchResponse:
        start_time = time.time()
        metadata = ResearchMetadata()

        async def emit(stage: str, message: str, pct: int, data: Optional[dict] = None):
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
                self._official_searcher_initialized = True

            # ── 1. 계획 수립 ──
            await emit("planning", "질의 분석 및 리서치 계획 수립 중...", 5)
            plan = await self.planner.plan(request.query, request.context)
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

            # ── 3. 초기 전문 추출 ──
            await emit("extracting", "웹 페이지 전문 추출 중...", 35)
            max_extract = request.max_sources or 30
            contents = await self.extractor.extract_from_results(results, max_extract=max_extract)
            all_contents.extend(contents)
            for c in contents:
                from urllib.parse import urlparse
                raw_storage.store(c.url, c.title, c.content,
                                  urlparse(c.url).netloc.lstrip("www."))
            await emit("extracting", f"{len(contents)}개 페이지 추출 완료", 50,
                      {"extracted_count": len(contents)})

            # ── 3a. 공식 소스 집중 검색 (비미국 관할 또는 cross-border) ──
            official_results_count = 0
            official_extracted_count = 0
            if jurisdiction.primary != "US" or jurisdiction.is_cross_border:
                sec_label = jurisdiction.primary + (
                    f"+{jurisdiction.secondary[0]}" if jurisdiction.secondary else ""
                )
                await emit("searching", f"공식 소스 집중 검색 ({sec_label})...", 32)
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
                            from urllib.parse import urlparse as _up
                            raw_storage.store(
                                c.url, c.title, c.content,
                                _up(c.url).netloc.lstrip("www."),
                            )
                        official_extracted_count = len(official_contents)
                        await emit(
                            "searching",
                            f"공식 소스 {official_results_count}건 수집 / "
                            f"{official_extracted_count}건 본문 추출",
                            36,
                        )
                except Exception as e:
                    logger.warning(f"[pipeline] 공식 소스 검색 실패 (계속): {e}")

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

                await emit("reflecting", f"정보 충분성 평가 (라운드 {iteration})...",
                          50 + iteration * 5)
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

                extra_contents = await self.extractor.extract_from_results(
                    extra_results, max_extract=10
                )
                all_contents.extend(extra_contents)
                for c in extra_contents:
                    from urllib.parse import urlparse
                    raw_storage.store(c.url, c.title, c.content,
                                      urlparse(c.url).netloc.lstrip("www."))
                await emit("extracting",
                          f"추가 {len(extra_contents)}개 페이지 추출", 75)

            # ── 5. 최종 보고서 생성 ──
            await emit("synthesizing", "최종 보고서 작성 중 (Gemini Pro)...", 80)

            elapsed = time.time() - start_time
            metadata.total_queries = self.searcher.total_queries
            metadata.elapsed_seconds = elapsed

            # 신뢰도 기준 콘텐츠 정렬 (상위 출처 우선 노출)
            all_contents = evidence_ranker.rank_contents(all_contents)

            # coverage 정보 생성
            collected_urls = [c.url for c in all_contents]
            coverage_dict = official_source_searcher.build_coverage_info(
                jurisdiction, collected_urls,
                official_extracted_count=official_extracted_count,
            )
            coverage = CoverageInfo(
                checked=coverage_dict["checked"],
                unchecked=coverage_dict["unchecked"],
                notes=coverage_dict["notes"],
            )

            await emit("synthesizing", f"환각 검증 준비 ({len(raw_storage)}개 원본 저장됨)...", 82)
            report = await self.synthesizer.synthesize(
                query=request.query,
                contents=all_contents,
                search_results=all_results,
                required_sections=plan.required_sections,
                metadata=metadata,
                job_id=job_id,
                raw_storage=raw_storage,
                coverage=coverage,
            )

            report.metadata.elapsed_seconds = time.time() - start_time
            report.metadata.estimated_cost_usd = (
                self.planner.estimated_cost + self.synthesizer.estimated_cost
            )

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


# ── 작업 관리 ──

def create_job(job_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = JobStatusResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        progress_pct=0,
        current_stage="pending",
        message="대기 중...",
    )
    _job_queues[job_id] = queue
    return queue


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
