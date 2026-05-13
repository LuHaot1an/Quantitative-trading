# Backend

FastAPI backend for the quantitative trading dashboard.

## Local Run

```bash
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

## Trading 212 Read-Only Setup

Set these environment variables on Render or locally:

```bash
TRADING212_BASE_URL=https://live.trading212.com/api/v0
TRADING212_API_KEY=...
TRADING212_API_SECRET=...
FRONTEND_ORIGINS=http://localhost:5173,https://your-frontend.onrender.com
```

The backend only implements account, portfolio, and strategy endpoints. It does not expose Trading 212 order endpoints.

