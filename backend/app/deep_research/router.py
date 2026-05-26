from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.deep_research.models import (
    DeepResearchRequest, DeepResearchResponse, JobStatusResponse
)
from app.deep_research.pipeline import (
    DeepResearchPipeline, create_job, get_job_status, stream_events
)
from app.deep_research.chat_service import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deep-research", tags=["deep-research"])
_pipeline = DeepResearchPipeline()


# ── 요청 모델 ──

class StockResearchRequest(BaseModel):
    query: str
    context: Optional[dict] = None

class PlanRequest(BaseModel):
    query: str
    internal_context: Optional[str] = None

class PlanRefineRequest(BaseModel):
    current_plan: str
    user_message: str

class SimpleChatRequest(BaseModel):
    question: str
    internal_context: Optional[str] = None
    history: Optional[list[dict]] = None

class ExecuteResearchRequest(BaseModel):
    query: str
    plan: str
    internal_context: Optional[str] = None

class SessionCreateRequest(BaseModel):
    ticker: str
    title: str
    mode: str = "deep"

class MessageSaveRequest(BaseModel):
    role: str
    content: str
    metadata: Optional[dict] = None


# ── 메인 리서치 엔드포인트 ──

@router.post("", response_model=JobStatusResponse)
async def start_research(request: DeepResearchRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    queue = create_job(job_id)
    async def _run():
        try:
            await _pipeline.run(request, job_id, queue)
        except Exception as e:
            logger.error(f"[router] 실패 job_id={job_id}: {e}")
    background_tasks.add_task(_run)
    return get_job_status(job_id)


@router.post("/stock/{ticker}", response_model=JobStatusResponse)
async def start_stock_research(
    ticker: str,
    request: StockResearchRequest,
    background_tasks: BackgroundTasks,
):
    from app.deep_research.sources.finvision_internal import FinVisionInternalSource
    job_id = str(uuid.uuid4())
    queue = create_job(job_id)
    async def _run():
        try:
            internal = FinVisionInternalSource()
            internal_context = await internal.fetch_stock_context(ticker.upper())
            dr_request = DeepResearchRequest(
                query=f"[{ticker.upper()} 종목 분석]\n{request.query}",
                context={"ticker": ticker.upper(), "finvision_data": internal_context, **(request.context or {})},
            )
            await _pipeline.run(dr_request, job_id, queue)
        except Exception as e:
            logger.error(f"[router] stock research 실패 {ticker}: {e}")
    background_tasks.add_task(_run)
    return get_job_status(job_id)


# ── 플랜 생성/수정 엔드포인트 ──

@router.post("/stock/{ticker}/plan")
async def generate_plan(ticker: str, request: PlanRequest):
    """Gemini만으로 리서치 계획 생성 (검색 API 사용 안 함)."""
    plan = await chat_service.generate_plan(
        ticker=ticker.upper(),
        query=request.query,
        internal_context=request.internal_context or "",
    )
    return {"plan": plan}


@router.post("/plan/refine")
async def refine_plan(request: PlanRefineRequest):
    """사용자 피드백으로 계획 수정."""
    refined = await chat_service.refine_plan(request.current_plan, request.user_message)
    return {"plan": refined}


@router.post("/stock/{ticker}/execute", response_model=JobStatusResponse)
async def execute_research(
    ticker: str,
    request: ExecuteResearchRequest,
    background_tasks: BackgroundTasks,
):
    """최종 승인 후 풀 리서치 실행."""
    from app.deep_research.sources.finvision_internal import FinVisionInternalSource
    job_id = str(uuid.uuid4())
    queue = create_job(job_id)
    async def _run():
        try:
            internal_context = request.internal_context or ""
            if not internal_context:
                internal = FinVisionInternalSource()
                internal_context = await internal.fetch_stock_context(ticker.upper())
            enhanced_query = (
                f"[{ticker.upper()} 심층 리서치]\n"
                f"사용자 질문: {request.query}\n\n"
                f"승인된 리서치 계획:\n{request.plan}"
            )
            dr_request = DeepResearchRequest(
                query=enhanced_query,
                context={"ticker": ticker.upper(), "finvision_data": internal_context},
            )
            await _pipeline.run(dr_request, job_id, queue)
        except Exception as e:
            logger.error(f"[router] execute 실패 {ticker}: {e}")
    background_tasks.add_task(_run)
    return get_job_status(job_id)


# ── 간단 채팅 엔드포인트 ──

@router.post("/stock/{ticker}/chat")
async def simple_chat(ticker: str, request: SimpleChatRequest):
    """심층 리서치 OFF 모드 — Gemini만 사용."""
    answer = await chat_service.simple_chat(
        ticker=ticker.upper(),
        question=request.question,
        internal_context=request.internal_context or "",
        history=request.history or [],
    )
    return {"answer": answer}


# ── 작업 상태/스트림 ──

@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_status(job_id: str):
    status = get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")
    return status


@router.get("/{job_id}/stream")
async def stream_progress(job_id: str):
    if get_job_status(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")
    return StreamingResponse(
        stream_events(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sync", response_model=DeepResearchResponse)
async def research_sync(request: DeepResearchRequest):
    job_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    try:
        return await _pipeline.run(request, job_id, queue)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 세션 관리 ──

@router.get("/sessions/{ticker}")
async def list_sessions(ticker: str):
    """티커별 채팅 세션 목록."""
    from app.database import DB_PATH
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM research_sessions WHERE ticker=? ORDER BY updated_at DESC LIMIT 50",
            (ticker.upper(),)
        )
        rows = await cursor.fetchall()
    return {"sessions": [dict(r) for r in rows]}


@router.post("/sessions")
async def create_session(request: SessionCreateRequest):
    """새 세션 생성."""
    from app.database import DB_PATH
    import aiosqlite
    session_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO research_sessions (id, ticker, title, mode, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (session_id, request.ticker.upper(), request.title, request.mode, now, now)
        )
        await db.commit()
    return {"session_id": session_id}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """세션 메시지 조회."""
    from app.database import DB_PATH
    import aiosqlite, json as _json
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM research_messages WHERE session_id=? ORDER BY created_at ASC",
            (session_id,)
        )
        rows = await cursor.fetchall()
    messages = []
    for r in rows:
        msg = dict(r)
        if msg.get("metadata"):
            try:
                msg["metadata"] = _json.loads(msg["metadata"])
            except Exception:
                pass
        messages.append(msg)
    return {"messages": messages}


@router.post("/sessions/{session_id}/messages")
async def save_message(session_id: str, request: MessageSaveRequest):
    """메시지 저장."""
    from app.database import DB_PATH
    import aiosqlite, json as _json
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO research_messages (session_id, role, content, metadata, created_at) VALUES (?,?,?,?,?)",
            (session_id, request.role, request.content,
             _json.dumps(request.metadata) if request.metadata else None, now)
        )
        await db.execute(
            "UPDATE research_sessions SET updated_at=? WHERE id=?",
            (now, session_id)
        )
        await db.commit()
    return {"ok": True}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """세션 삭제."""
    from app.database import DB_PATH
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM research_messages WHERE session_id=?", (session_id,))
        await db.execute("DELETE FROM research_sessions WHERE id=?", (session_id,))
        await db.commit()
    return {"ok": True}
