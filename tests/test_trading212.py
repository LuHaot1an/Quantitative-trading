from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.trading212 import Trading212Client, Trading212Error


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_status_does_not_expose_credentials():
    client = Trading212Client(api_key="secret-key", api_secret="secret-value")

    status = client.status()

    assert status["configured"] is True
    assert status["can_trade"] is False
    assert "secret" not in str(status)


def test_positions_are_read_from_portfolio_endpoint(monkeypatch):
    calls = []

    def fake_get(url, auth, timeout):
        calls.append((url, auth, timeout))
        return FakeResponse(200, [{"ticker": "AAPL_US_EQ", "quantity": 2, "currentPrice": 100}])

    monkeypatch.setattr("backend.app.trading212.requests.get", fake_get)
    client = Trading212Client(api_key="key", api_secret="value", base_url="https://live.trading212.com/api/v0")

    positions = client.positions()

    assert positions[0]["ticker"] == "AAPL_US_EQ"
    assert calls[0][0] == "https://live.trading212.com/api/v0/equity/portfolio"
    assert calls[0][1] == ("key", "value")


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "密钥无效"),
        (403, "拒绝访问"),
        (408, "请求超时"),
        (429, "接口限流"),
    ],
)
def test_trading212_errors_are_beginner_readable(monkeypatch, status_code, expected):
    def fake_get(url, auth, timeout):
        return FakeResponse(status_code, {"error": "raw"})

    monkeypatch.setattr("backend.app.trading212.requests.get", fake_get)
    client = Trading212Client(api_key="key", api_secret="value")

    with pytest.raises(Trading212Error) as exc_info:
        client.positions()

    assert expected in str(exc_info.value)
    assert "key" not in str(exc_info.value)
    assert "value" not in str(exc_info.value)


def test_api_does_not_expose_order_routes():
    api = TestClient(app)
    paths = {route.path for route in app.routes}

    assert not any("/orders" in path for path in paths)
    assert api.get("/api/broker/trading212/status").status_code == 200


def test_app_password_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr("backend.app.main.APP_PASSWORD", "correct-password")
    api = TestClient(app)

    response = api.get("/api/broker/trading212/status", headers={"X-App-Password": "wrong"})

    assert response.status_code == 401
    assert "correct-password" not in response.text
