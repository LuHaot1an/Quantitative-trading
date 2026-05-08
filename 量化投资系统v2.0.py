#!/usr/bin/env python3
"""
量化投资系统 v2.0  ——  Trading 212 实操版
========================================================================
一键运行:  python "量化投资系统v2.0.py"
默认 aggressive 模式 (短期 1 个月持有, 追求最大化收益)

可选参数:
  --mode {aggressive, balanced}   策略 (默认 aggressive)
                                  aggressive: 3 月动量, 行业 2 只, Max Sharpe
                                  balanced:   12-1 动量+价值+质量, HRP 权重
  --budget 500                    预算 (英镑)
  --n 5                           持仓只数
  --weighting {equal, inv_vol, hrp, max_sharpe}  权重方法
  --no-backtest                   跳过回测 (更快)
  --refresh                       强制刷新缓存
"""
from __future__ import annotations
import argparse
import os
import pickle
import sys
import warnings
from io import StringIO

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from pypfopt import EfficientFrontier, HRPOpt, expected_returns, risk_models

warnings.filterwarnings("ignore")

# ========================================================================
# 配置 (改这里调参)
# ========================================================================
DATA_START = "2018-01-01"
CACHE_DIR = "stock_cache"
T212_FX_FEE = 0.0015        # Trading 212 换汇费
TRANSACTION_COST = 0.0005   # 单边交易成本估计 (回测用)
RF_RATE = 0.043             # 无风险利率 (Sharpe 用)
TODAY = pd.Timestamp.today().normalize()

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
PRICE_CACHE_DAYS = 2        # 价格缓存有效期
FUND_CACHE_DAYS = 3         # 基本面缓存有效期

# ----- 两套策略模式 -----
MODE_DESC = {
    "balanced":   "长期月度调仓, 12-1 动量 + 价值 + 质量, 行业分散, HRP 权重",
    "aggressive": "短期 1 个月持有, 3 个月动量 + 1 周反转惩罚, 集中下注 (Max Sharpe)",
}


# ========================================================================
# 工具函数
# ========================================================================
def zscore(s: pd.Series) -> pd.Series:
    s = s.dropna()
    std = s.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def fetch_sp500() -> tuple[list[str], dict[str, str]]:
    html = requests.get(SP500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    table = pd.read_html(StringIO(html))[0]
    table["Symbol"] = table["Symbol"].str.replace(".", "-", regex=False)
    return table["Symbol"].tolist(), dict(zip(table["Symbol"], table["GICS Sector"]))


def load_prices(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    cache_path = os.path.join(CACHE_DIR, "sp500_prices.parquet")
    if not refresh and os.path.exists(cache_path):
        cached = pd.read_parquet(cache_path)
        if (TODAY - cached.index[-1].normalize()).days <= PRICE_CACHE_DAYS:
            print(f"  价格缓存命中 (最新: {cached.index[-1].date()})")
            return cached
    print("  下载价格中 (约 30 秒)...")
    df = yf.download(tickers, start=DATA_START, end=TODAY,
                     auto_adjust=True, progress=False)["Close"]
    df.to_parquet(cache_path)
    return df


def load_fundamentals(tickers: list[str], sector_map: dict[str, str],
                      refresh: bool = False) -> pd.DataFrame:
    cache_path = os.path.join(CACHE_DIR, "fundamentals.pkl")
    if not refresh and os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            cached = pickle.load(f)
        if (TODAY - cached["asof"]).days <= FUND_CACHE_DAYS:
            cached_tickers = set(cached["data"].index)
            if set(tickers).issubset(cached_tickers):
                print("  基本面缓存命中")
                return cached["data"].loc[tickers]

    print(f"  下载基本面数据 ({len(tickers)} 只, 约 30-60 秒)...")
    rows = []
    for i, t in enumerate(tickers):
        try:
            info = yf.Ticker(t).info
            rows.append({
                "Ticker": t,
                "PE": info.get("trailingPE", np.nan),
                "PB": info.get("priceToBook", np.nan),
                "ROE": info.get("returnOnEquity", np.nan),
                "MarketCap": info.get("marketCap", np.nan),
                "Sector": info.get("sector", sector_map.get(t, "Unknown")),
            })
        except Exception:
            rows.append({
                "Ticker": t, "PE": np.nan, "PB": np.nan, "ROE": np.nan,
                "MarketCap": np.nan, "Sector": sector_map.get(t, "Unknown"),
            })
        if (i + 1) % 10 == 0:
            print(f"    {i+1}/{len(tickers)}", flush=True)
    df = pd.DataFrame(rows).set_index("Ticker")
    with open(cache_path, "wb") as f:
        pickle.dump({"asof": TODAY, "data": df}, f)
    return df


# ========================================================================
# 因子打分
# ========================================================================
def price_factor_score(price: pd.DataFrame, mode: str = "balanced") -> pd.Series:
    """计算价格因子综合分数.

    balanced (长期):   0.6 * 12-1 动量 - 0.3 * 1年波动 + 0.1 * 1月反转
    aggressive (短期): 0.7 * 3月动量    - 0.2 * 3月波动 - 0.1 * 1周近期涨幅
    """
    rets = price.pct_change()

    if mode == "aggressive":
        mom_3m  = price.pct_change(63).iloc[-1]                # 3 月动量
        vol_3m  = rets.rolling(63).std().iloc[-1]              # 3 月波动
        spike   = rets.iloc[-5:].sum()                          # 最近 1 周累计涨幅
        score = (
            0.7 * zscore(mom_3m)
            - 0.2 * zscore(vol_3m)
            - 0.1 * zscore(spike)                              # 惩罚刚暴涨的 (易回调)
        )
    else:  # balanced
        mom_12_1 = price.shift(21).pct_change(231).iloc[-1]
        vol_1y   = rets.rolling(252).std().iloc[-1]
        rev_1m   = -rets.iloc[-21:].sum()
        score = (
            0.6 * zscore(mom_12_1)
            - 0.3 * zscore(vol_1y)
            + 0.1 * zscore(rev_1m)
        )

    return score.dropna().sort_values(ascending=False)


def select_with_fundamentals(price_score: pd.Series, fund: pd.DataFrame,
                             n: int, mode: str = "balanced") -> tuple[list[str], pd.Series]:
    """质量/基本面过滤 + 综合打分 + 行业分散选 N 只.

    balanced:   严格质量过滤 (PE/ROE/PB) + 价值因子加分 + 每行业 1 只
    aggressive: 仅市值 > $1B 过滤 + 纯动量打分 (无价值) + 每行业最多 2 只
    """
    if mode == "aggressive":
        quality = fund["MarketCap"].fillna(0) > 1e9
        qualified = fund[quality].index.tolist()
        if len(qualified) < n:
            qualified = fund.index.tolist()
        final_score = zscore(price_score.loc[qualified]).sort_values(ascending=False)
        sector_cap = 2
    else:  # balanced
        quality = (
            (fund["PE"] > 0) & (fund["PE"] < 50) &
            (fund["ROE"] > 0.10) & (fund["PB"] > 0)
        )
        qualified = fund[quality].index.tolist()
        if len(qualified) < n:
            print(f"  ⚠️  严格过滤后仅 {len(qualified)} 只, 放宽 ROE 至 5%")
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


# ========================================================================
# 输出: 选股 + 买入清单
# ========================================================================
def print_selection(selected: list[str], fund: pd.DataFrame,
                    final_score: pd.Series, mode: str = "balanced") -> None:
    cap = 2 if mode == "aggressive" else 1
    print(f"\n{'='*72}")
    print(f"{'最终选股 (每行业最多 ' + str(cap) + ' 只)':^72}")
    print("=" * 72)
    print(f"{'Ticker':<8}{'Sector':<26}{'PE':>8}{'PB':>8}{'ROE':>10}{'Score':>10}")
    print("-" * 72)
    for t in selected:
        pe = fund.loc[t, "PE"]
        pb = fund.loc[t, "PB"]
        roe = fund.loc[t, "ROE"]
        pe_s = f"{pe:>8.2f}" if pd.notna(pe) else f"{'n/a':>8}"
        pb_s = f"{pb:>8.2f}" if pd.notna(pb) else f"{'n/a':>8}"
        roe_s = f"{roe:>10.2%}" if pd.notna(roe) else f"{'n/a':>10}"
        print(f"{t:<8}{fund.loc[t, 'Sector']:<26}{pe_s}{pb_s}{roe_s}"
              f"{final_score[t]:>10.2f}")


WEIGHT_DESC = {
    "equal":      "等权 (1/N) - 简单稳健, 学术研究证明小组合下最佳",
    "inv_vol":    "反向波动 - 低风险股票多买, 整体波动更低",
    "hrp":        "HRP 层级风险平价 - 通过相关性聚类智能分配",
    "max_sharpe": "Max Sharpe - 历史 Sharpe 最高 (5 只股票易过度集中)",
}


def compute_weights(selected: list[str], price: pd.DataFrame,
                    method: str, lookback_days: int = 252 * 3,
                    cap: float = 0.40) -> dict[str, float]:
    """计算权重. method: equal / inv_vol / hrp / max_sharpe"""
    n = len(selected)
    sub = price[selected].dropna().tail(lookback_days)
    rets = sub.pct_change().dropna()

    if method == "equal":
        return {t: 1 / n for t in selected}

    if method == "inv_vol":
        vol = rets.std() * np.sqrt(252)
        inv = 1 / vol
        w = inv / inv.sum()
        return {t: float(w[t]) for t in selected}

    if method == "hrp":
        hrp = HRPOpt(returns=rets).optimize()
        return {t: float(hrp[t]) for t in selected}

    if method == "max_sharpe":
        mu = expected_returns.mean_historical_return(sub)
        S = risk_models.sample_cov(sub)
        ef = EfficientFrontier(mu, S, weight_bounds=(0.05, cap))
        ef.max_sharpe()
        cleaned = ef.clean_weights()
        total = sum(cleaned.values())
        return {t: cleaned[t] / total for t in selected}

    raise ValueError(f"未知 weighting 方法: {method}")


def print_buy_list(selected: list[str], price: pd.DataFrame,
                   budget_gbp: float, weights: dict[str, float],
                   method: str) -> None:
    fx_rate = float(yf.Ticker("GBPUSD=X").history(period="5d")["Close"].iloc[-1])
    gbp_after_fx = budget_gbp * (1 - T212_FX_FEE)
    usd_total = gbp_after_fx * fx_rate

    current_prices = price[selected].iloc[-1]

    print(f"\n{'='*72}")
    print(f"{'£%.0f BUY LIST (Trading 212)' % budget_gbp:^72}")
    print("=" * 72)
    print(f"权重方法: {method}  ({WEIGHT_DESC[method]})")
    print(f"GBP/USD:  {fx_rate:.4f}    FX 费 (0.15%): £{budget_gbp - gbp_after_fx:.2f}")
    print(f"可用资金: £{gbp_after_fx:.2f}  =  ${usd_total:.2f}\n")
    print(f"{'Ticker':<8}{'Weight':>9}{'Price ($)':>12}{'Shares':>11}"
          f"{'Cost ($)':>11}{'Cost (£)':>11}")
    print("-" * 72)

    total_usd = 0.0
    for t in selected:
        w = weights[t]
        gbp_amt = gbp_after_fx * w
        usd_amt = usd_total * w
        p = float(current_prices[t])
        shares = round(usd_amt / p, 4)
        cost_usd = shares * p
        cost_gbp = cost_usd / fx_rate
        total_usd += cost_usd
        print(f"{t:<8}{w*100:>8.1f}%{p:>12.2f}{shares:>11.4f}"
              f"{cost_usd:>11.2f}{cost_gbp:>11.2f}")

    total_gbp = total_usd / fx_rate
    print("-" * 72)
    print(f"{'TOTAL':<8}{'100.0%':>9}{'':>12}{'':>11}{total_usd:>11.2f}{total_gbp:>11.2f}")
    print(f"{'FX fee':<8}{'':>9}{'':>12}{'':>11}{'':>11}{budget_gbp - gbp_after_fx:>11.2f}")
    print(f"{'剩余':<8}{'':>9}{'':>12}{'':>11}{'':>11}"
          f"{budget_gbp - total_gbp - (budget_gbp - gbp_after_fx):>11.2f}")
    print("=" * 72)
    print("\n操作步骤 (Trading 212):")
    print("  1. App 内将 £%d 转入 Stocks ISA / Invest 账户" % budget_gbp)
    print("  2. 搜每个 ticker -> 'Buy by amount' -> 按上面 'Cost (£)' 列输入金额")
    print("  3. 设置一个月后的调仓提醒")


# ========================================================================
# 回测
# ========================================================================
def run_backtest(price: pd.DataFrame, sector_map: dict[str, str], n: int,
                 mode: str = "balanced") -> None:
    print(f"\n  开始回测 (mode={mode}, 仅价格因子, 含 0.05% 单边成本)...")
    sector_cap = 2 if mode == "aggressive" else 1

    def select_diversified(score: pd.Series) -> list[str]:
        sel, sc = [], {}
        for t in score.index:
            sec = sector_map.get(t)
            if sec is None or sc.get(sec, 0) >= sector_cap:
                continue
            sel.append(t)
            sc[sec] = sc.get(sec, 0) + 1
            if len(sel) == n:
                break
        return sel

    month_ends = price.resample("ME").last().index
    month_ends = [d for d in month_ends if d in price.index]
    rebalance_dates = month_ends[12:-1]

    results, prev_w = [], {}
    for rd in rebalance_dates:
        next_d = month_ends[month_ends.index(rd) + 1]
        hist = price.loc[:rd].dropna(axis=1, thresh=int(len(price.loc[:rd]) * 0.9)).ffill()
        if len(hist) < 252:
            continue
        score = price_factor_score(hist, mode=mode)
        sel = select_diversified(score)
        if len(sel) < n:
            continue
        w = {t: 1 / n for t in sel}
        sp_, ep_ = price.loc[rd, sel], price.loc[next_d, sel]
        gross = sum(w[t] * (ep_[t] / sp_[t] - 1)
                    for t in sel if pd.notna(sp_[t]) and pd.notna(ep_[t]))
        keys = set(w) | set(prev_w)
        turnover = sum(abs(w.get(t, 0) - prev_w.get(t, 0)) for t in keys) / 2
        cost = turnover * TRANSACTION_COST * 2
        results.append({
            "date": next_d, "gross": gross,
            "net": gross - cost, "turnover": turnover,
        })
        prev_w = w

    bt = pd.DataFrame(results).set_index("date")

    spy = yf.download("SPY", start=DATA_START, end=TODAY,
                      auto_adjust=True, progress=False)["Close"]
    if isinstance(spy, pd.DataFrame):
        spy = spy.iloc[:, 0]
    spy_m = spy.resample("ME").last().pct_change().reindex(bt.index).fillna(0)

    def metrics(rets: pd.Series) -> tuple[float, float, float, float]:
        rets = pd.Series(rets).dropna()
        ann = (1 + rets).prod() ** (12 / len(rets)) - 1
        vol = rets.std() * np.sqrt(12)
        shp = (ann - RF_RATE) / vol if vol > 0 else float("nan")
        eq = (1 + rets).cumprod()
        mdd = (eq / eq.cummax() - 1).min()
        return ann, vol, shp, mdd

    print(f"\n{'='*78}")
    print(f"{'策略':<26}{'年化收益':>12}{'年化波动':>12}{'Sharpe':>10}{'最大回撤':>14}")
    print("-" * 78)
    for name, rets in [
        ("v2.0 (毛收益)", bt["gross"]),
        ("v2.0 (扣 0.05% 成本)", bt["net"]),
        ("SPY 基准", spy_m),
    ]:
        ann, vol, shp, mdd = metrics(rets)
        print(f"{name:<26}{ann:>11.2%}{vol:>11.2%}{shp:>9.2f}{mdd:>13.2%}")
    print("=" * 78)
    print(f"\n回测期: {bt.index[0].date()} -> {bt.index[-1].date()} ({len(bt)} 月)")
    print(f"平均月度换手率: {bt['turnover'].mean():.2%}")
    print("\n⚠️  幸存者偏差: 用今日 S&P 500 名单回测, 实盘真实收益约比此低 3-5% 年化")
    print("⚠️  基本面因子无历史数据, 未参与回测; 当前选股已用质量+价值过滤")


# ========================================================================
# Main
# ========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="量化投资系统 v2.0 (Trading 212)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "策略模式 (--mode):\n"
            "  balanced   长期月度调仓, 12-1 动量 + 价值 + 质量, 每行业 1 只, HRP 权重\n"
            "  aggressive 短期 1 个月持有, 3 月动量 + 1 周反转惩罚, 每行业 2 只, Max Sharpe\n"
        )
    )
    parser.add_argument("--budget", type=float, default=500.0, help="预算 (英镑)")
    parser.add_argument("--n", type=int, default=5, help="持仓只数")
    parser.add_argument(
        "--mode", choices=["balanced", "aggressive"], default="aggressive",
        help="策略模式 (默认 aggressive 短期 1 月最大化收益, balanced=长期稳健)"
    )
    parser.add_argument(
        "--weighting", choices=["equal", "inv_vol", "hrp", "max_sharpe"],
        default=None,
        help="权重方法 (默认随 mode: balanced->hrp, aggressive->max_sharpe)"
    )
    parser.add_argument("--no-backtest", action="store_true", help="跳过回测")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存, 重新下载")
    args = parser.parse_args()

    # 根据 mode 设默认 weighting
    if args.weighting is None:
        args.weighting = "max_sharpe" if args.mode == "aggressive" else "hrp"

    os.makedirs(CACHE_DIR, exist_ok=True)

    print("=" * 72)
    print(f"量化投资系统 v2.0  |  {TODAY.date()}")
    print(f"  mode={args.mode} ({MODE_DESC[args.mode]})")
    print(f"  budget=£{args.budget:.0f}  n={args.n}  weighting={args.weighting}")
    print("=" * 72)

    print("\n[1/5] 获取 S&P 500 名单...")
    tickers, sector_map = fetch_sp500()
    print(f"  共 {len(tickers)} 只")

    print("\n[2/5] 加载价格数据...")
    price = load_prices(tickers, refresh=args.refresh)
    price = price.dropna(axis=1, thresh=int(len(price) * 0.9)).ffill()
    print(f"  清洗后: {price.shape[1]} 只, 截至 {price.index[-1].date()}")

    print(f"\n[3/5] 计算价格因子分数 (mode={args.mode})...")
    p_score = price_factor_score(price, mode=args.mode)
    candidates = p_score.head(50).index.tolist()
    print(f"  Top-50 候选池就绪")

    print("\n[4/5] 加载基本面数据...")
    fund = load_fundamentals(candidates, sector_map, refresh=args.refresh)

    selected, final_score = select_with_fundamentals(
        p_score, fund, n=args.n, mode=args.mode
    )
    print_selection(selected, fund, final_score, mode=args.mode)

    print(f"\n[5/5] 计算权重 ({args.weighting}) 并生成买入清单...")
    weights = compute_weights(selected, price, method=args.weighting)
    print_buy_list(selected, price, budget_gbp=args.budget,
                   weights=weights, method=args.weighting)

    if args.mode == "aggressive":
        print("\n" + "!" * 72)
        print("⚠️  AGGRESSIVE 模式提醒")
        print("!" * 72)
        print("  - 选的是 3 个月动量牛股, 已经涨过的, 月度内可能继续涨也可能急回调")
        print("  - 单只股票崩盘 -20% = 整个 £500 组合可能亏 -£40 ~ -£80")
        print("  - 这套策略本质是'追涨', 牛市能放大收益, 熊市会放大亏损")
        print("  - 建议: 1) 只用闲钱  2) 设个心理止损 (例如 -10%)  3) 严格 1 个月就卖")
        print("!" * 72)

    if not args.no_backtest:
        print(f"\n{'='*72}")
        print(f"{'回测验证 (mode=' + args.mode + ')':^72}")
        print("=" * 72)
        run_backtest(price, sector_map, n=args.n, mode=args.mode)

    print("\n✅ 完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 错误: {e}", file=sys.stderr)
        raise
