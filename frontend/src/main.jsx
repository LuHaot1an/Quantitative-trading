import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Database,
  LineChart,
  Lock,
  RefreshCw,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const defaultSettings = {
  budget_gbp: 0,
  n: 5,
  mode: "aggressive",
  weighting: "inv_vol",
  refresh: false,
};

const COLORS = ["#2563eb", "#16a34a", "#dc2626", "#ca8a04", "#0891b2", "#7c3aed", "#be123c"];

function formatMoney(value, currency = "GBP") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency, maximumFractionDigits: 2 }).format(value);
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

async function api(path, options = {}) {
  const appPassword = sessionStorage.getItem("APP_PASSWORD") || "";
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", "X-App-Password": appPassword },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function App() {
  const [appPassword, setAppPassword] = useState(sessionStorage.getItem("APP_PASSWORD") || "");
  const [passwordInput, setPasswordInput] = useState(sessionStorage.getItem("APP_PASSWORD") || "");
  const [settings, setSettings] = useState(defaultSettings);
  const [strategy, setStrategy] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [brokerStatus, setBrokerStatus] = useState(null);
  const [account, setAccount] = useState(null);
  const [positions, setPositions] = useState([]);
  const [rebalance, setRebalance] = useState(null);
  const [manualRows, setManualRows] = useState([{ ticker: "AAPL", quantity: 0, current_price: 0 }]);
  const [loading, setLoading] = useState(false);
  const [loadingLabel, setLoadingLabel] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (appPassword) {
      refreshBrokerStatus();
    }
  }, [appPassword]);

  function unlockApp(event) {
    event.preventDefault();
    sessionStorage.setItem("APP_PASSWORD", passwordInput);
    setAppPassword(passwordInput);
  }

  async function refreshBrokerStatus() {
    try {
      setBrokerStatus(await api("/api/broker/trading212/status"));
    } catch (err) {
      setBrokerStatus({ configured: false, message: err.message, can_trade: false });
    }
  }

  async function runDashboard(nextSettings = settings) {
    setLoading(true);
    setLoadingLabel("正在按现有持仓和追加资金计算调仓，第一次运行可能需要 1-3 分钟。");
    setError("");
    try {
      if (positions.length) {
        const result = await api("/api/portfolio/rebalance/manual", {
          method: "POST",
          body: JSON.stringify({ ...nextSettings, positions }),
        });
        setStrategy(result.strategy);
        setRebalance(result.rebalance);
      } else {
        const payload = JSON.stringify(nextSettings);
        const strategyData = await api("/api/strategy/run", { method: "POST", body: payload });
        setStrategy(strategyData);
      }
      setBacktest(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setLoadingLabel("");
    }
  }

  async function syncLivePortfolio() {
    setLoading(true);
    setLoadingLabel("正在同步 Trading 212 持仓，只读取账户和持仓，不会交易。");
    setError("");
    try {
      const [accountData, positionData] = await Promise.all([
        api("/api/broker/trading212/account"),
        api("/api/broker/trading212/positions"),
      ]);
      setAccount(accountData);
      setPositions(positionData);
    } catch (err) {
      setError(`${err.message} 可使用下方手动录入。`);
    } finally {
      setLoading(false);
      setLoadingLabel("");
    }
  }

  async function runManualRebalance() {
    setLoading(true);
    setLoadingLabel("正在用手动持仓计算调仓。");
    setError("");
    try {
      const cleanPositions = manualRows
        .filter((row) => row.ticker.trim())
        .map((row) => ({
          ticker: row.ticker.trim().toUpperCase(),
          quantity: Number(row.quantity) || 0,
          current_price: Number(row.current_price) || 0,
        }));
      const result = await api("/api/portfolio/rebalance/manual", {
        method: "POST",
        body: JSON.stringify({ ...settings, positions: cleanPositions }),
      });
      setRebalance(result.rebalance);
      setStrategy(result.strategy);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setLoadingLabel("");
    }
  }

  const allocationData = useMemo(
    () =>
      (strategy?.holdings || []).map((item) => ({
        name: item.ticker,
        weight: Number((item.weight * 100).toFixed(1)),
        value: item.target_value_gbp,
      })),
    [strategy],
  );

  if (!appPassword) {
    return (
      <main className="lock-screen">
        <form className="login-panel" onSubmit={unlockApp}>
          <Lock size={28} />
          <h1>输入访问密码</h1>
          <p className="muted">这个网页会读取 Trading 212 账户和持仓，必须先输入你设置的 APP_PASSWORD。</p>
          <input
            type="password"
            value={passwordInput}
            onChange={(event) => setPasswordInput(event.target.value)}
            placeholder="APP_PASSWORD"
            autoFocus
          />
          <button className="primary" type="submit" disabled={!passwordInput}>进入看板</button>
        </form>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">Trading 212 Live Read Only</p>
          <h1>量化投资调仓看板</h1>
        </div>
        <div className="security-strip">
          <span><Lock size={16} /> API 密钥只在后端环境变量</span>
          <span><ShieldCheck size={16} /> 不提供自动交易入口</span>
        </div>
      </section>

      <section className="status-band">
        <StatusCard icon={<Database />} label="数据日期" value={strategy?.as_of || "-"} />
        <StatusCard icon={<Wallet />} label="追加资金" value={formatMoney(settings.budget_gbp)} />
        <StatusCard icon={<LineChart />} label="策略模式" value={settings.mode === "aggressive" ? "激进动量" : "均衡质量"} />
        <StatusCard icon={<CheckCircle2 />} label="Trading 212" value={brokerStatus?.configured ? "已配置只读" : "未配置"} />
      </section>

      <section className="workbench">
        <aside className="control-panel">
          <h2>策略设置</h2>
          <label>
            追加资金 GBP
            <input
              type="number"
              min="0"
              value={settings.budget_gbp}
              onChange={(event) => setSettings({ ...settings, budget_gbp: Number(event.target.value) })}
            />
          </label>
          <label>
            持仓数量
            <input
              type="number"
              min="2"
              max="20"
              value={settings.n}
              onChange={(event) => setSettings({ ...settings, n: Number(event.target.value) })}
            />
          </label>
          <label>
            策略
            <select value={settings.mode} onChange={(event) => setSettings({ ...settings, mode: event.target.value })}>
              <option value="aggressive">激进动量</option>
              <option value="balanced">均衡质量</option>
            </select>
          </label>
          <label>
            权重
            <select value={settings.weighting} onChange={(event) => setSettings({ ...settings, weighting: event.target.value })}>
              <option value="inv_vol">反向波动</option>
              <option value="hrp">HRP 风险平价</option>
              <option value="equal">等权</option>
              <option value="max_sharpe">最大 Sharpe</option>
            </select>
          </label>
          <button className="primary" onClick={() => runDashboard()} disabled={loading}>
            <RefreshCw size={17} /> 重新计算策略
          </button>
          <button className="secondary" onClick={syncLivePortfolio} disabled={loading || !brokerStatus?.configured}>
            <Wallet size={17} /> 同步 Trading 212 持仓
          </button>
          <p className="muted">{brokerStatus?.message || "正在检测 Trading 212 配置。"}</p>
        </aside>

        <section className="main-grid">
          {error && <div className="alert"><AlertTriangle size={18} /> {error}</div>}
          {loadingLabel && <div className="notice"><RefreshCw size={18} /> {loadingLabel}</div>}
          {strategy && (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <p className="eyebrow">Target Allocation</p>
                    <h2>今日建议仓位</h2>
                  </div>
                  <span className="pill">只读建议</span>
                </div>
                <div className="split">
                  <div className="chart-box">
                    <ResponsiveContainer width="100%" height={260}>
                      <BarChart data={allocationData}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                        <XAxis dataKey="name" />
                        <YAxis />
                        <Tooltip formatter={(value) => `${value}%`} />
                        <Bar dataKey="weight" radius={[5, 5, 0, 0]}>
                          {allocationData.map((entry, index) => (
                            <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                <div className="metric-stack">
                    <Metric label="目标组合总额" value={formatMoney(strategy.gbp_after_fx)} />
                    <Metric label="GBP/USD" value={Number(strategy.fx_rate_gbpusd).toFixed(4)} />
                    <Metric label="追加资金" value={formatMoney(settings.budget_gbp)} />
                  </div>
                </div>
                <HoldingsTable holdings={strategy.holdings} />
              </section>

              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <p className="eyebrow">Backtest</p>
                    <h2>历史表现和风险</h2>
                  </div>
                  <BarChart3 size={22} />
                </div>
                <RiskMetrics backtest={backtest} />
                <div className="chart-box">
                  <ResponsiveContainer width="100%" height={260}>
                    <AreaChart data={backtest?.series || []}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="date" minTickGap={28} />
                      <YAxis />
                      <Tooltip formatter={(value) => Number(value).toFixed(2)} />
                      <Area type="monotone" dataKey="strategy" stroke="#2563eb" fill="#dbeafe" name="策略" />
                      <Area type="monotone" dataKey="spy" stroke="#16a34a" fill="#dcfce7" name="SPY" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <ul className="warnings">
                  {(strategy.warnings || []).map((warning) => <li key={warning}>{warning}</li>)}
                </ul>
              </section>
            </>
          )}

          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Portfolio</p>
                <h2>我的持仓和调仓差额</h2>
              </div>
              <span className="pill">Live 只读模式</span>
            </div>
            <AccountSummary account={account} positions={positions} />
            <PositionsTable positions={positions} />
            <ManualInput rows={manualRows} setRows={setManualRows} onRun={runManualRebalance} loading={loading} />
            <RebalanceTable rebalance={rebalance} />
          </section>
        </section>
      </section>
    </main>
  );
}

function StatusCard({ icon, label, value }) {
  return (
    <div className="status-card">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function HoldingsTable({ holdings }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>行业</th>
            <th>仓位</th>
            <th>买入金额</th>
            <th>股数</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((item) => (
            <tr key={item.ticker}>
              <td><strong>{item.ticker}</strong></td>
              <td>{item.sector}</td>
              <td>{formatPercent(item.weight)}</td>
              <td>{formatMoney(item.target_value_gbp)}</td>
              <td>{item.shares}</td>
              <td>{item.explanation}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RiskMetrics({ backtest }) {
  const metrics = backtest?.metrics?.strategy || {};
  const sharpe = metrics.sharpe;
  return (
    <div className="metric-row">
      <Metric label="年化收益" value={formatPercent(metrics.annual_return)} />
      <Metric label="年化波动" value={formatPercent(metrics.annual_volatility)} />
      <Metric label="Sharpe" value={sharpe === null || sharpe === undefined ? "-" : sharpe.toFixed(2)} />
      <Metric label="最大回撤" value={formatPercent(metrics.max_drawdown)} />
    </div>
  );
}

function AccountSummary({ account, positions }) {
  const cash = account?.cash || {};
  const currency = cash.currencyCode || account?.info?.currencyCode || "GBP";
  const freeCash = cash.free ?? cash.available ?? cash.cash;
  return (
    <div className="account-grid">
      <Metric label="账户币种" value={currency || "-"} />
      <Metric label="可用现金" value={freeCash === null || freeCash === undefined ? "-" : formatMoney(freeCash, currency)} />
      <Metric label="持仓数量" value={positions.length || "-"} />
    </div>
  );
}

function PositionsTable({ positions }) {
  if (!positions.length) {
    return <p className="muted">点击“同步 Trading 212 持仓”后，这里会先显示你的真实账户持仓。</p>;
  }
  return (
    <div className="table-wrap compact-table">
      <table>
        <thead>
          <tr>
            <th>真实持仓</th>
            <th>数量</th>
            <th>现价</th>
            <th>盈亏</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((item, index) => (
            <tr key={`${item.ticker || item.shortName || index}`}>
              <td><strong>{item.ticker || item.shortName || "-"}</strong></td>
              <td>{item.quantity ?? "-"}</td>
              <td>{item.currentPrice ?? item.averagePrice ?? "-"}</td>
              <td>{item.ppl ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ManualInput({ rows, setRows, onRun, loading }) {
  function update(index, key, value) {
    setRows(rows.map((row, rowIndex) => (rowIndex === index ? { ...row, [key]: value } : row)));
  }

  return (
    <div className="manual-box">
      <div className="manual-heading">
        <h3>手动录入备用</h3>
        <button className="ghost" onClick={() => setRows([...rows, { ticker: "", quantity: 0, current_price: 0 }])}>添加一行</button>
      </div>
      {rows.map((row, index) => (
        <div className="manual-row" key={`${index}-${row.ticker}`}>
          <input placeholder="Ticker" value={row.ticker} onChange={(event) => update(index, "ticker", event.target.value)} />
          <input type="number" placeholder="股数" value={row.quantity} onChange={(event) => update(index, "quantity", event.target.value)} />
          <input type="number" placeholder="现价" value={row.current_price} onChange={(event) => update(index, "current_price", event.target.value)} />
        </div>
      ))}
      <button className="secondary" onClick={onRun} disabled={loading}>用手动持仓计算调仓</button>
    </div>
  );
}

function RebalanceTable({ rebalance }) {
  if (!rebalance) {
    return <p className="muted">同步 Trading 212 或手动录入后，这里会显示需要买入、卖出或保持的金额。</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>动作</th>
            <th>当前金额</th>
            <th>目标金额</th>
            <th>差额</th>
          </tr>
        </thead>
        <tbody>
          {rebalance.rows.map((row) => (
            <tr key={row.ticker}>
              <td><strong>{row.ticker}</strong></td>
              <td><span className={`action ${row.action}`}>{actionText(row.action)}</span></td>
              <td>{formatMoney(row.current_value)}</td>
              <td>{formatMoney(row.target_value)}</td>
              <td>{formatMoney(row.difference)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="metric-row rebalance-summary">
        <Metric label="目标组合总额" value={formatMoney(rebalance.total_target_value)} />
        <Metric label="需要买入" value={formatMoney(rebalance.cash_needed_if_only_buying)} />
        <Metric label="需要卖出" value={formatMoney(rebalance.cash_released_by_sells)} />
        <Metric label="净追加/回收" value={formatMoney(rebalance.cash_needed_if_only_buying - rebalance.cash_released_by_sells)} />
      </div>
      <p className="muted">{rebalance.note}</p>
    </div>
  );
}

function actionText(action) {
  if (action === "buy") return "买入";
  if (action === "sell") return "卖出";
  return "保持";
}

createRoot(document.getElementById("root")).render(<App />);
