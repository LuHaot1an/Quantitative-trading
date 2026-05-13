from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .strategy import run_strategy

CACHE_PATH = Path("/tmp/quant_strategy_cache.json")
CACHE_TTL_SECONDS = 6 * 60 * 60

_LOCK = threading.Lock()
_SCHEDULER_STARTED = False
_STATE: dict[str, Any] = {
    "running": False,
    "last_success": None,
    "last_error": None,
    "cache": None,
}


def get_precompute_status() -> dict[str, Any]:
    cache = get_cached_strategy()
    with _LOCK:
        return {
            "running": _STATE["running"],
            "last_success": _STATE["last_success"],
            "last_error": _STATE["last_error"],
            "has_cache": cache is not None,
            "cache_age_seconds": int(time.time() - cache["computed_at"]) if cache else None,
        }


def get_cached_strategy() -> dict[str, Any] | None:
    with _LOCK:
        cache = _STATE.get("cache")
    if cache and time.time() - cache["computed_at"] <= CACHE_TTL_SECONDS:
        return cache["strategy"]

    if CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - raw.get("computed_at", 0) <= CACHE_TTL_SECONDS:
            with _LOCK:
                _STATE["cache"] = raw
            return raw["strategy"]
    return None


def start_precompute(force: bool = False) -> dict[str, Any]:
    if not force and get_cached_strategy() is not None:
        return get_precompute_status()

    with _LOCK:
        if _STATE["running"]:
            return get_precompute_status()
        _STATE["running"] = True
        _STATE["last_error"] = None

    thread = threading.Thread(target=_precompute_worker, daemon=True)
    thread.start()
    return get_precompute_status()


def start_scheduler() -> None:
    global _SCHEDULER_STARTED
    with _LOCK:
        if _SCHEDULER_STARTED:
            return
        _SCHEDULER_STARTED = True
    thread = threading.Thread(target=_scheduler_worker, daemon=True)
    thread.start()


def scale_cached_strategy(strategy: dict[str, Any], budget_gbp: float) -> dict[str, Any]:
    base_budget = float(strategy.get("gbp_after_fx") or strategy.get("budget_gbp") or 1)
    scale = budget_gbp / base_budget if base_budget else 0
    scaled = json.loads(json.dumps(strategy))
    scaled["budget_gbp"] = budget_gbp
    scaled["gbp_after_fx"] = budget_gbp
    scaled["fx_fee_gbp"] = 0
    for holding in scaled.get("holdings", []):
        holding["target_value_gbp"] = float(holding.get("target_value_gbp", 0)) * scale
        holding["target_value_usd"] = float(holding.get("target_value_usd", 0)) * scale
        price_usd = float(holding.get("price_usd") or 0)
        fx_rate = float(scaled.get("fx_rate_gbpusd") or 1)
        holding["shares"] = round((holding["target_value_gbp"] * fx_rate) / price_usd, 4) if price_usd else 0
    return scaled


def _precompute_worker() -> None:
    try:
        strategy = run_strategy(
            budget_gbp=1000.0,
            n=5,
            mode="aggressive",
            weighting="inv_vol",
            refresh=False,
            fast=False,
        )
        cache = {"computed_at": time.time(), "strategy": strategy}
        CACHE_PATH.write_text(json.dumps(cache))
        with _LOCK:
            _STATE["cache"] = cache
            _STATE["last_success"] = cache["computed_at"]
    except Exception as exc:
        with _LOCK:
            _STATE["last_error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["running"] = False


def _scheduler_worker() -> None:
    start_precompute(force=False)
    while True:
        time.sleep(CACHE_TTL_SECONDS)
        start_precompute(force=False)
