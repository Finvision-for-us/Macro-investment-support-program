"""portfolio API 통합 테스트 (임시 DB 격리 + yfinance 네트워크 스텁).

라우터 ↔ DB(aiosqlite) 배선을 실제 HTTP 호출로 검증한다.
실제 finvision.db·외부 API를 건드리지 않는다.
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_mod
import app.database as database
from app.services import yfinance_client


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_file = str(tmp_path / "test.db")
    # DB_PATH를 임시 파일로 (database + portfolio 양쪽 바인딩 모두 교체)
    monkeypatch.setattr(database, "DB_PATH", db_file)
    monkeypatch.setattr(portfolio_mod, "DB_PATH", db_file)
    # 네트워크 차단: yfinance 조회를 결정론 스텁으로
    monkeypatch.setattr(
        yfinance_client,
        "get_overview",
        lambda t: {"name": f"{t} Inc", "current_price": 100.0},
    )
    asyncio.run(database.init_db())

    api = FastAPI()
    api.include_router(portfolio_mod.router)
    return TestClient(api)


def test_starts_empty(client):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_add_then_list(client):
    r = client.post(
        "/api/portfolio",
        json={"ticker": "aapl", "buy_price": 150.0, "quantity": 10},
    )
    assert r.status_code == 200
    assert r.json()["ticker"] == "AAPL"  # 대문자 정규화

    items = client.get("/api/portfolio").json()["items"]
    assert len(items) == 1
    assert items[0]["ticker"] == "AAPL"
    assert items[0]["invested"] == 1500.0          # 150 * 10
    assert items[0]["current_value"] == 1000.0     # 스텁가 100 * 10
    assert items[0]["profit_loss"] == -500.0


def test_delete(client):
    client.post(
        "/api/portfolio",
        json={"ticker": "MSFT", "buy_price": 300.0, "quantity": 5},
    )
    item_id = client.get("/api/portfolio").json()["items"][0]["id"]

    r = client.delete(f"/api/portfolio/{item_id}")
    assert r.status_code == 200
    assert client.get("/api/portfolio").json()["items"] == []


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/api/portfolio/99999")
    assert r.status_code == 404
