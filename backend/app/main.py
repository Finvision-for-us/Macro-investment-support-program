from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import init_db
from app.api import macro, stock, portfolio, earnings, telegram, stories
from app.deep_research.router import router as deep_research_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 경기 사이클 패턴 예열(백그라운드) — 첫 market-state 요청이 FRED 대량
    # 조회(수십 초~2분)에 걸려 프론트 타임아웃되는 것 방지
    from app.services.business_cycle import warm_cache_async
    warm_cache_async()
    yield

app = FastAPI(title="FinVision API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(macro.router)
app.include_router(stock.router)
app.include_router(portfolio.router)
app.include_router(earnings.router)
app.include_router(telegram.router)
app.include_router(stories.router)
app.include_router(deep_research_router)

@app.get("/")
def root():
    return {"status": "ok", "message": "FinVision API"}
