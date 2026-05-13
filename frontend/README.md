# Frontend

React/Vite frontend for the quantitative trading dashboard.

## Local Run

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

## Render

Deploy as a Render Static Site. Set `VITE_API_BASE_URL` to the deployed FastAPI service URL, for example:

```text
https://quant-trading-api.onrender.com
```

