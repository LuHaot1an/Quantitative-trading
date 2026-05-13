from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import FRONTEND_ORIGINS
from .models import BacktestRequest, LiveRebalanceRequest, ManualRebalanceRequest, StrategyRequest
from .strategy import rebalance_from_positions, run_backtest_summary, run_strategy
from .trading212 import Trading212Client, Trading212Error

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="Quantitative Trading Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/strategy/run")
def api_strategy_run(request: StrategyRequest) -> dict:
    return run_strategy(
        budget_gbp=request.budget_gbp,
        n=request.n,
        mode=request.mode,
        weighting=request.weighting,
        refresh=request.refresh,
    )


@app.post("/api/backtest")
def api_backtest(request: BacktestRequest) -> dict:
    return run_backtest_summary(n=request.n, mode=request.mode, weighting=request.weighting)


@app.get("/api/broker/trading212/status")
def api_trading212_status() -> dict:
    return Trading212Client().status()


@app.get("/api/broker/trading212/account")
def api_trading212_account() -> dict:
    try:
        return Trading212Client().account()
    except Trading212Error as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@app.get("/api/broker/trading212/positions")
def api_trading212_positions() -> list[dict]:
    try:
        return Trading212Client().positions()
    except Trading212Error as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@app.post("/api/portfolio/rebalance/live")
def api_rebalance_live(request: LiveRebalanceRequest) -> dict:
    try:
        client = Trading212Client()
        account = client.account()
        positions = client.positions()
    except Trading212Error as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc

    strategy = run_strategy(
        budget_gbp=request.budget_gbp,
        n=request.n,
        mode=request.mode,
        weighting=request.weighting,
        refresh=request.refresh,
    )
    currency = (account.get("info") or {}).get("currencyCode") or "GBP"
    return {"strategy": strategy, "account": account, "rebalance": rebalance_from_positions(strategy, positions, currency)}


@app.post("/api/portfolio/rebalance/manual")
def api_rebalance_manual(request: ManualRebalanceRequest) -> dict:
    strategy = run_strategy(
        budget_gbp=request.budget_gbp,
        n=request.n,
        mode=request.mode,
        weighting=request.weighting,
        refresh=request.refresh,
    )
    positions = [position.model_dump() for position in request.positions]
    return {"strategy": strategy, "rebalance": rebalance_from_positions(strategy, positions, "GBP")}

