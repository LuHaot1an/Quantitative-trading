# Quantitative Trading Dashboard

前后端分离的量化投资看板，基于现有 `量化投资系统v2.0.py` 的选股、权重和回测逻辑，并新增 Trading 212 Live 只读持仓同步。

## 功能

- FastAPI 后端：策略计算、回测、Trading 212 只读账户/持仓读取、调仓差额计算。
- React 前端：推荐仓位、回测图表、风险指标、实时持仓、手动持仓备用录入。
- Render 部署：`render.yaml` 定义后端 Web Service 和前端 Static Site。

## 安全边界

- Trading 212 密钥只放后端环境变量。
- 前端不会保存或显示 API Key/API Secret。
- 不实现、不暴露 Trading 212 下单、改单、撤单接口。
- 结果是投资辅助信息，不保证盈利。

## 本地运行

后端：

```bash
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

## Render 环境变量

后端设置：

```text
TRADING212_BASE_URL=https://live.trading212.com/api/v0
TRADING212_API_KEY=你的 Trading 212 API Key
TRADING212_API_SECRET=你的 Trading 212 API Secret
FRONTEND_ORIGINS=https://你的前端域名.onrender.com
```

前端设置：

```text
VITE_API_BASE_URL=https://你的后端域名.onrender.com
```
