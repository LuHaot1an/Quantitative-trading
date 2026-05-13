from __future__ import annotations

from backend.app.strategy import rebalance_from_positions


def test_rebalance_marks_buy_sell_hold_and_extra_position():
    strategy = {
        "gbp_after_fx": 1000.0,
        "holdings": [
            {"ticker": "AAPL", "target_value_gbp": 300.0},
            {"ticker": "MSFT", "target_value_gbp": 300.0},
            {"ticker": "NVDA", "target_value_gbp": 400.0},
        ],
    }
    positions = [
        {"ticker": "AAPL_US_EQ", "quantity": 1, "currentPrice": 300.0},
        {"ticker": "MSFT_US_EQ", "quantity": 1, "currentPrice": 500.0},
        {"ticker": "TSLA_US_EQ", "quantity": 1, "currentPrice": 50.0},
    ]

    result = rebalance_from_positions(strategy, positions)
    actions = {row["ticker"]: row["action"] for row in result["rows"]}

    assert actions["AAPL"] == "hold"
    assert actions["MSFT"] == "sell"
    assert actions["NVDA"] == "buy"
    assert actions["TSLA"] == "sell"
    assert result["cash_needed_if_only_buying"] == 400.0
    assert result["cash_released_by_sells"] == 250.0

