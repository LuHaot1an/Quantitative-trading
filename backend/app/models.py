from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Mode = Literal["aggressive", "balanced"]
Weighting = Literal["equal", "inv_vol", "hrp", "max_sharpe"]


class StrategyRequest(BaseModel):
    budget_gbp: float = Field(default=0.0, ge=0)
    n: int = Field(default=5, ge=2, le=20)
    mode: Mode = "aggressive"
    weighting: Weighting | None = None
    refresh: bool = False


class BacktestRequest(BaseModel):
    n: int = Field(default=5, ge=2, le=20)
    mode: Mode = "aggressive"
    weighting: Weighting | None = None


class ManualPosition(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    quantity: float = 0
    current_price: float | None = None
    market_value: float | None = None
    average_price: float | None = None


class ManualRebalanceRequest(StrategyRequest):
    positions: list[ManualPosition] = []


class LiveRebalanceRequest(StrategyRequest):
    pass
