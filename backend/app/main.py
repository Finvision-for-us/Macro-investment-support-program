from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import init_db
from app.api import macro, stock, portfolio, earnings
from app.deep_research.router import router as deep_research_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
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
app.include_router(deep_research_router)

@app.get("/")
def root():
    return {"status": "ok", "message": "FinVision API"}
