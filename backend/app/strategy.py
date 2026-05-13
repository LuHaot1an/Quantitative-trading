from __future__ import annotations

import time
from io import StringIO
from typing import Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from pypfopt import EfficientFrontier, HRPOpt, expected_returns, risk_models

from .config import (
    DATA_START,
    DEFAULT_MAX_WEIGHT,
    RF_RATE,
    SP500_URL,
    T212_FX_FEE,
    TRANSACTION_COST,
)


MODE_DESC = {
    "balanced": "长期月度调仓, 12-1 动量 + 价值 + 质量, 行业分散, HRP 权重",
    "aggressive": "短期 1 个月持有, 3 月动量 + 风险/估值惩罚, 限制单股集中度",
}

WEIGHT_DESC = {
    "equal": "等权: 每只股票买差不多金额，最容易理解",
    "inv_vol": "反向波动: 波动小的股票多买一点，降低组合起伏",
    "hrp": "层级风险平价: 根据相关性分散风险",
    "max_sharpe": "最大 Sharpe: 追求历史风险收益比，可能更集中",
}

_CACHE: dict[str, tuple[float, Any]] = {}


def _now() -> float:
    return time.time()


def _cache_get(key: str, ttl_seconds: int, refresh: bool) -> Any | None:
    if refresh:
        return None
    item = _CACHE.get(key)
    if not item:
        return None
    created_at, value = item
    if _now() - created_at > ttl_seconds:
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    _CACHE[key] = (_now(), value)
    return value


def _clean_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def zscore(s: pd.Series) -> pd.Series:
    s = s.dropna()
    std = s.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def cap_and_normalize(weights: pd.Series, cap: float) -> dict[str, float]:
    w = weights.astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)
    if w.sum() <= 0:
        w = pd.Series(1.0, index=weights.index)
    w = w / w.sum()

    if cap * len(w) < 1:
        raise ValueError(f"cap={cap:.2f} 太低, {len(w)} 只股票无法分配 100% 权重")

    capped = pd.Series(0.0, index=w.index)
    free = w.copy()
    remaining = 1.0

    while len(free) > 0:
        scaled = free / free.sum() * remaining
        over = scaled > cap
        if not over.any():
            capped.loc[scaled.index] = scaled
            break
        capped.loc[scaled[over].index] = cap
        remaining = 1.0 - capped.sum()
        free = free.loc[~over]

    return {t: float(capped[t]) for t in weights.index}


def default_weighting(mode: str, weighting: str | None) -> str:
    if weighting:
        return weighting
    return "inv_vol" if mode == "aggressive" else "hrp"


def fetch_sp500(refresh: bool = False) -> tuple[list[str], dict[str, str]]:
    cached = _cache_get("sp500", 24 * 60 * 60, refresh)
    if cached is not None:
        return cached
    html = requests.get(SP500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    table = pd.read_html(StringIO(html))[0]
    table["Symbol"] = table["Symbol"].str.replace(".", "-", regex=False)
    value = (table["Symbol"].tolist(), dict(zip(table["Symbol"], table["GICS Sector"])))
    return _cache_set("sp500", value)


def load_prices(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    cached = _cache_get("prices", 2 * 24 * 60 * 60, refresh)
    if cached is not None:
        return cached
    today = pd.Timestamp.today().normalize()
    df = yf.download(tickers, start=DATA_START, end=today, auto_adjust=True, progress=False)["Close"]
    df = df.dropna(axis=1, thresh=int(len(df) * 0.9)).ffill()
    return _cache_set("prices", df)


def load_fundamentals(
    tickers: list[str],
    sector_map: dict[str, str],
    refresh: bool = False,
) -> pd.DataFrame:
    cache_key = "fundamentals:" + ",".join(sorted(tickers))
    cached = _cache_get(cache_key, 3 * 24 * 60 * 60, refresh)
    if cached is not None:
        return cached

    rows = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            rows.append(
                {
                    "Ticker": t,
                    "PE": info.get("trailingPE", np.nan),
                    "PB": info.get("priceToBook", np.nan),
                    "ROE": info.get("returnOnEquity", np.nan),
                    "MarketCap": info.get("marketCap", np.nan),
                    "Sector": info.get("sector", sector_map.get(t, "Unknown")),
                }
            )
        except Exception:
            rows.append(
                {
                    "Ticker": t,
                    "PE": np.nan,
                    "PB": np.nan,
                    "ROE": np.nan,
                    "MarketCap": np.nan,
                    "Sector": sector_map.get(t, "Unknown"),
                }
            )
    return _cache_set(cache_key, pd.DataFrame(rows).set_index("Ticker"))


def price_factor_score(price: pd.DataFrame, mode: str = "balanced") -> pd.Series:
    rets = price.pct_change()
    if mode == "aggressive":
        mom_3m = price.pct_change(63).iloc[-1]
        vol_3m = rets.rolling(63).std().iloc[-1]
        spike = rets.iloc[-5:].sum()
        score = 0.7 * zscore(mom_3m) - 0.2 * zscore(vol_3m) - 0.1 * zscore(spike)
    else:
        mom_12_1 = price.shift(21).pct_change(231).iloc[-1]
        vol_1y = rets.rolling(252).std().iloc[-1]
        rev_1m = -rets.iloc[-21:].sum()
        score = 0.6 * zscore(mom_12_1) - 0.3 * zscore(vol_1y) + 0.1 * zscore(rev_1m)
    return score.dropna().sort_values(ascending=False)


def select_with_fundamentals(
    price_score: pd.Series,
    fund: pd.DataFrame,
    n: int,
    mode: str = "balanced",
) -> tuple[list[str], pd.Series]:
    if mode == "aggressive":
        pe_ok = fund["PE"].isna() | ((fund["PE"] > 0) & (fund["PE"] < 150))
        roe_ok = fund["ROE"].isna() | (fund["ROE"] > -0.05)
        quality = (fund["MarketCap"].fillna(0) > 5e9) & pe_ok & roe_ok
        qualified = fund[quality].index.tolist()
        if len(qualified) < n:
            qualified = fund[fund["MarketCap"].fillna(0) > 1e9].index.tolist()
        q = fund.loc[qualified]
        valuation_penalty = pd.Series(0.0, index=qualified)
        valuation_penalty += q["PE"].clip(lower=0, upper=150).fillna(75) / 150
        valuation_penalty += q["PB"].clip(lower=0, upper=100).fillna(20) / 100
        roe_median = q["ROE"].median()
        roe_score = zscore(q["ROE"].fillna(0 if pd.isna(roe_median) else roe_median))
        final_score = (
            0.85 * zscore(price_score.loc[qualified])
            + 0.15 * roe_score
            - 0.20 * zscore(valuation_penalty)
        ).sort_values(ascending=False)
        sector_cap = 2
    else:
        quality = (fund["PE"] > 0) & (fund["PE"] < 50) & (fund["ROE"] > 0.10) & (fund["PB"] > 0)
        qualified = fund[quality].index.tolist()
        if len(qualified) < n:
            quality = (fund["PE"] > 0) & (fund["ROE"] > 0.05) & (fund["PB"] > 0)
            qualified = fund[quality].index.tolist()
        ep = 1 / fund.loc[qualified, "PE"]
        bp = 1 / fund.loc[qualified, "PB"]
        final_score = (
            0.5 * zscore(price_score.loc[qualified])
            + 0.3 * zscore(ep)
            + 0.2 * zscore(bp)
        ).sort_values(ascending=False)
        sector_cap = 1

    selected, sector_count = [], {}
    for t in final_score.index:
        sec = fund.loc[t, "Sector"]
        if sector_count.get(sec, 0) >= sector_cap:
            continue
        selected.append(t)
        sector_count[sec] = sector_count.get(sec, 0) + 1
        if len(selected) == n:
            break
    return selected, final_score


def compute_weights(
    selected: list[str],
    price: pd.DataFrame,
    method: str,
    lookback_days: int = 252 * 3,
    cap: float = DEFAULT_MAX_WEIGHT,
) -> dict[str, float]:
    sub = price[selected].dropna().tail(lookback_days)
    rets = sub.pct_change().dropna()
    if method == "equal":
        return cap_and_normalize(pd.Series(1.0, index=selected), cap)
    if method == "inv_vol":
        return cap_and_normalize(1 / (rets.std() * np.sqrt(252)), cap)
    if method == "hrp":
        return cap_and_normalize(pd.Series(HRPOpt(returns=rets).optimize()), cap)
    if method == "max_sharpe":
        mu = expected_returns.mean_historical_return(sub)
        cov = risk_models.CovarianceShrinkage(sub).ledoit_wolf()
        ef = EfficientFrontier(mu, cov, weight_bounds=(0.05, cap))
        ef.max_sharpe()
        cleaned = ef.clean_weights()
        total = sum(cleaned.values())
        return {t: cleaned[t] / total for t in selected}
    raise ValueError(f"未知 weighting 方法: {method}")


def current_fx_rate(refresh: bool = False) -> float:
    cached = _cache_get("fx:gbpusd", 60 * 60, refresh)
    if cached is not None:
        return cached
    fx_rate = float(yf.Ticker("GBPUSD=X").history(period="5d")["Close"].iloc[-1])
    return _cache_set("fx:gbpusd", fx_rate)


def run_strategy(
    budget_gbp: float,
    n: int,
    mode: str = "aggressive",
    weighting: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    weighting = default_weighting(mode, weighting)
    tickers, sector_map = fetch_sp500(refresh=refresh)
    price = load_prices(tickers, refresh=refresh)
    price_score = price_factor_score(price, mode=mode)
    candidates = price_score.head(50).index.tolist()
    fund = load_fundamentals(candidates, sector_map, refresh=refresh)
    selected, final_score = select_with_fundamentals(price_score, fund, n=n, mode=mode)
    weights = compute_weights(selected, price, method=weighting)

    fx_rate = current_fx_rate(refresh=refresh)
    gbp_after_fx = budget_gbp * (1 - T212_FX_FEE)
    usd_total = gbp_after_fx * fx_rate
    current_prices = price[selected].iloc[-1]

    holdings = []
    for ticker in selected:
        weight = weights[ticker]
        usd_amount = usd_total * weight
        price_usd = float(current_prices[ticker])
        shares = round(usd_amount / price_usd, 4)
        holdings.append(
            {
                "ticker": ticker,
                "sector": str(fund.loc[ticker, "Sector"]),
                "weight": weight,
                "price_usd": price_usd,
                "shares": shares,
                "target_value_gbp": usd_amount / fx_rate,
                "target_value_usd": usd_amount,
                "score": _clean_float(final_score[ticker]),
                "pe": _clean_float(fund.loc[ticker, "PE"]),
                "pb": _clean_float(fund.loc[ticker, "PB"]),
                "roe": _clean_float(fund.loc[ticker, "ROE"]),
                "explanation": _explain_pick(mode, fund.loc[ticker, "Sector"], weights[ticker]),
            }
        )

    return {
        "as_of": str(price.index[-1].date()),
        "mode": mode,
        "mode_description": MODE_DESC[mode],
        "weighting": weighting,
        "weighting_description": WEIGHT_DESC[weighting],
        "budget_gbp": budget_gbp,
        "gbp_after_fx": gbp_after_fx,
        "fx_rate_gbpusd": fx_rate,
        "fx_fee_gbp": budget_gbp - gbp_after_fx,
        "holdings": holdings,
        "warnings": [
            "历史回测不代表未来收益，尤其是 aggressive 动量策略。",
            "这个系统只提供调仓辅助，不保证盈利，也不会自动下单。",
            "单只股票可能快速下跌，建议只使用能承受亏损的资金。",
        ],
    }


def run_backtest_summary(n: int, mode: str = "aggressive", weighting: str | None = None) -> dict[str, Any]:
    weighting = default_weighting(mode, weighting)
    tickers, sector_map = fetch_sp500(refresh=False)
    price = load_prices(tickers, refresh=False)
    sector_cap = 2 if mode == "aggressive" else 1

    def select_diversified(score: pd.Series) -> list[str]:
        selected, sector_count = [], {}
        for ticker in score.index:
            sector = sector_map.get(ticker)
            if sector is None or sector_count.get(sector, 0) >= sector_cap:
                continue
            selected.append(ticker)
            sector_count[sector] = sector_count.get(sector, 0) + 1
            if len(selected) == n:
                break
        return selected

    month_ends = price.resample("ME").last().index
    month_ends = [d for d in month_ends if d in price.index]
    results, prev_w = [], {}
    for rebalance_date in month_ends[12:-1]:
        next_date = month_ends[month_ends.index(rebalance_date) + 1]
        hist = price.loc[:rebalance_date].dropna(axis=1, thresh=int(len(price.loc[:rebalance_date]) * 0.9)).ffill()
        if len(hist) < 252:
            continue
        selected = select_diversified(price_factor_score(hist, mode=mode))
        if len(selected) < n:
            continue
        try:
            weights = compute_weights(selected, hist, method=weighting)
        except Exception:
            weights = {ticker: 1 / n for ticker in selected}
        start_prices = price.loc[rebalance_date, selected]
        end_prices = price.loc[next_date, selected]
        gross = sum(
            weights[ticker] * (end_prices[ticker] / start_prices[ticker] - 1)
            for ticker in selected
            if pd.notna(start_prices[ticker]) and pd.notna(end_prices[ticker])
        )
        keys = set(weights) | set(prev_w)
        turnover = sum(abs(weights.get(ticker, 0) - prev_w.get(ticker, 0)) for ticker in keys) / 2
        cost = turnover * TRANSACTION_COST * 2
        results.append({"date": next_date, "gross": gross, "net": gross - cost, "turnover": turnover})
        prev_w = weights

    if not results:
        return {"series": [], "metrics": {}, "message": "回测数据不足"}

    bt = pd.DataFrame(results).set_index("date")
    spy = yf.download("SPY", start=DATA_START, end=pd.Timestamp.today().normalize(), auto_adjust=True, progress=False)["Close"]
    if isinstance(spy, pd.DataFrame):
        spy = spy.iloc[:, 0]
    spy_m = spy.resample("ME").last().pct_change().reindex(bt.index).fillna(0)

    def metrics(rets: pd.Series) -> dict[str, float | None]:
        values = pd.Series(rets).dropna()
        ann = (1 + values).prod() ** (12 / len(values)) - 1
        vol = values.std() * np.sqrt(12)
        sharpe = (ann - RF_RATE) / vol if vol > 0 else np.nan
        equity = (1 + values).cumprod()
        max_drawdown = (equity / equity.cummax() - 1).min()
        return {
            "annual_return": _clean_float(ann),
            "annual_volatility": _clean_float(vol),
            "sharpe": _clean_float(sharpe),
            "max_drawdown": _clean_float(max_drawdown),
        }

    strategy_equity = (1 + bt["net"]).cumprod()
    spy_equity = (1 + spy_m).cumprod()
    return {
        "mode": mode,
        "weighting": weighting,
        "series": [
            {
                "date": str(index.date()),
                "strategy": _clean_float(strategy_equity.loc[index]),
                "spy": _clean_float(spy_equity.loc[index]),
                "monthly_return": _clean_float(bt.loc[index, "net"]),
                "turnover": _clean_float(bt.loc[index, "turnover"]),
            }
            for index in bt.index
        ],
        "metrics": {
            "strategy": metrics(bt["net"]),
            "spy": metrics(spy_m),
            "average_turnover": _clean_float(bt["turnover"].mean()),
        },
        "warnings": [
            "回测使用今日 S&P 500 成分股，存在幸存者偏差。",
            "基本面历史数据未参与回测，真实表现可能低于回测。",
        ],
    }


def rebalance_from_positions(
    strategy: dict[str, Any],
    positions: list[dict[str, Any]],
    account_currency: str = "GBP",
) -> dict[str, Any]:
    targets = {item["ticker"]: item for item in strategy["holdings"]}
    actual: dict[str, dict[str, Any]] = {}
    total_current = 0.0
    for raw in positions:
        ticker = normalize_t212_ticker(str(raw.get("ticker", "")))
        market_value = _position_value(raw)
        total_current += market_value
        actual[ticker] = {
            "ticker": ticker,
            "raw_ticker": raw.get("ticker"),
            "quantity": _clean_float(raw.get("quantity")) or 0.0,
            "average_price": _clean_float(raw.get("averagePrice") or raw.get("average_price")),
            "current_price": _clean_float(raw.get("currentPrice") or raw.get("current_price")),
            "market_value": market_value,
            "ppl": _clean_float(raw.get("ppl")),
        }

    total_target = float(strategy["gbp_after_fx"])
    rows = []
    all_tickers = sorted(set(targets) | set(actual))
    for ticker in all_tickers:
        target_value = float(targets.get(ticker, {}).get("target_value_gbp", 0.0))
        current_value = float(actual.get(ticker, {}).get("market_value", 0.0))
        diff = target_value - current_value
        if abs(diff) < max(1.0, total_target * 0.005):
            action = "hold"
        else:
            action = "buy" if diff > 0 else "sell"
        rows.append(
            {
                "ticker": ticker,
                "action": action,
                "target_value": target_value,
                "current_value": current_value,
                "difference": diff,
                "target_weight": target_value / total_target if total_target else 0,
                "current_weight": current_value / total_current if total_current else 0,
                "position": actual.get(ticker),
            }
        )

    return {
        "account_currency": account_currency,
        "total_current_value": total_current,
        "total_target_value": total_target,
        "cash_needed_if_only_buying": sum(max(row["difference"], 0) for row in rows),
        "cash_released_by_sells": sum(abs(min(row["difference"], 0)) for row in rows),
        "rows": rows,
        "note": "这是调仓差额，不会自动下单。卖出/买入前请在 Trading 212 里再次核对价格。",
    }


def normalize_t212_ticker(ticker: str) -> str:
    ticker = ticker.strip().upper()
    for suffix in ("_US_EQ", "_EQ", ".US"):
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)].replace(".", "-")
    return ticker.replace(".", "-")


def _position_value(raw: dict[str, Any]) -> float:
    explicit = _clean_float(raw.get("market_value") or raw.get("marketValue"))
    if explicit is not None:
        return explicit
    quantity = _clean_float(raw.get("quantity")) or 0.0
    price = _clean_float(raw.get("currentPrice") or raw.get("current_price")) or 0.0
    return quantity * price


def _explain_pick(mode: str, sector: Any, weight: float) -> str:
    style = "短期动量更强" if mode == "aggressive" else "长期质量和估值更均衡"
    return f"{style}，属于 {sector} 行业；目标仓位约 {weight:.1%}。"

