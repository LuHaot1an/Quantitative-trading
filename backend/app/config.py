from __future__ import annotations

import os


APP_PASSWORD = os.getenv("APP_PASSWORD", "")
TRADING212_API_KEY = os.getenv("TRADING212_API_KEY", "")
TRADING212_API_SECRET = os.getenv("TRADING212_API_SECRET", "")
TRADING212_BASE_URL = os.getenv(
    "TRADING212_BASE_URL",
    "https://live.trading212.com/api/v0",
).rstrip("/")

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

DATA_START = "2018-01-01"
T212_FX_FEE = 0.0015
TRANSACTION_COST = 0.0005
RF_RATE = 0.043
DEFAULT_MAX_WEIGHT = 0.30
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
