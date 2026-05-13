from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import APP_PASSWORD, FRONTEND_ORIGINS
from .models import BacktestRequest, LiveRebalanceRequest, ManualRebalanceRequest, StrategyRequest
from .precompute import get_cached_strategy, get_precompute_status, scale_cached_strategy, start_precompute, start_scheduler
from .strategy import FAST_UNIVERSE, normalize_t212_ticker, rebalance_from_positions, run_backtest_summary, run_strategy
from .trading212 import Trading212Client, Trading212Error

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="Quantitative Trading Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-App-Password"],
)


@app.on_event("startup")
def startup_precompute() -> None:
    start_scheduler()


def require_app_password(x_app_password: str | None = Header(default=None)) -> None:
    if APP_PASSWORD and x_app_password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="访问密码错误。")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/precompute/status", dependencies=[Depends(require_app_password)])
def api_precompute_status() -> dict:
    return get_precompute_status()


@app.post("/api/precompute/run", dependencies=[Depends(require_app_password)])
def api_precompute_run() -> dict:
    return start_precompute(force=True)


@app.post("/api/strategy/run", dependencies=[Depends(require_app_password)])
def api_strategy_run(request: StrategyRequest) -> dict:
    if request.budget_gbp <= 0:
        raise HTTPException(status_code=400, detail="没有同步持仓时，追加资金必须大于 0。")
    cached = get_cached_strategy()
    if cached is None:
        start_precompute(force=False)
        raise HTTPException(status_code=202, detail="完整 S&P 500 策略正在后台计算，请稍后刷新状态。")
    return scale_cached_strategy(cached, request.budget_gbp)


@app.post("/api/backtest", dependencies=[Depends(require_app_password)])
def api_backtest(request: BacktestRequest) -> dict:
    return run_backtest_summary(n=request.n, mode=request.mode, weighting=request.weighting)


@app.get("/api/broker/trading212/status", dependencies=[Depends(require_app_password)])
def api_trading212_status() -> dict:
    return Trading212Client().status()


@app.get("/api/broker/trading212/account", dependencies=[Depends(require_app_password)])
def api_trading212_account() -> dict:
    try:
        return Trading212Client().account()
    except Trading212Error as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@app.get("/api/broker/trading212/positions", dependencies=[Depends(require_app_password)])
def api_trading212_positions() -> list[dict]:
    try:
        return Trading212Client().positions()
    except Trading212Error as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@app.post("/api/portfolio/rebalance/live", dependencies=[Depends(require_app_password)])
def api_rebalance_live(request: LiveRebalanceRequest) -> dict:
    try:
        client = Trading212Client()
        account = client.account()
        positions = client.positions()
    except Trading212Error as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc

    target_budget = portfolio_target_budget(positions, account, request.budget_gbp)
    cached = get_cached_strategy()
    if cached is None:
        start_precompute(force=False)
        raise HTTPException(status_code=202, detail="完整 S&P 500 策略正在后台计算，请稍后再试。")
    strategy = scale_cached_strategy(cached, target_budget)
    currency = (account.get("info") or {}).get("currencyCode") or "GBP"
    return {
        "strategy": strategy,
        "account": account,
        "rebalance": rebalance_from_positions(strategy, positions, currency),
        "additional_cash_gbp": request.budget_gbp,
    }


@app.post("/api/portfolio/rebalance/manual", dependencies=[Depends(require_app_password)])
def api_rebalance_manual(request: ManualRebalanceRequest) -> dict:
    positions = [position.model_dump() for position in request.positions]
    target_budget = portfolio_target_budget(positions, None, request.budget_gbp)
    if target_budget <= 0:
        raise HTTPException(status_code=400, detail="请先同步/录入持仓，或填写大于 0 的追加资金。")
    cached = get_cached_strategy()
    if cached is None:
        start_precompute(force=False)
        raise HTTPException(status_code=202, detail="完整 S&P 500 策略正在后台计算，请稍后再试。")
    strategy = scale_cached_strategy(cached, target_budget)
    return {
        "strategy": strategy,
        "rebalance": rebalance_from_positions(strategy, positions, "GBP"),
        "additional_cash_gbp": request.budget_gbp,
    }


def portfolio_target_budget(positions: list[dict], account: dict | None, additional_cash_gbp: float) -> float:
    current_value = sum(position_value(position) for position in positions)
    cash = 0.0
    if account:
        raw_cash = account.get("cash") or {}
        cash = float(raw_cash.get("free") or raw_cash.get("available") or raw_cash.get("cash") or 0)
    return current_value + cash + additional_cash_gbp


def portfolio_universe(positions: list[dict]) -> list[str]:
    current = [normalize_t212_ticker(str(position.get("ticker", ""))) for position in positions]
    return sorted(set(FAST_UNIVERSE) | {ticker for ticker in current if ticker})


def position_value(position: dict) -> float:
    explicit = position.get("market_value") or position.get("marketValue") or position.get("currentValue") or position.get("value")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(position.get("quantity") or 0) * float(position.get("currentPrice") or position.get("current_price") or 0)
    except (TypeError, ValueError):
        return 0.0
