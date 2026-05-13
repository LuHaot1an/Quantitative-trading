from __future__ import annotations

import logging
from typing import Any

import requests

from . import config

logger = logging.getLogger(__name__)


class Trading212Error(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class Trading212Client:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        timeout: int = 15,
    ):
        self.api_key = api_key if api_key is not None else config.TRADING212_API_KEY
        self.api_secret = api_secret if api_secret is not None else config.TRADING212_API_SECRET
        self.base_url = (base_url if base_url is not None else config.TRADING212_BASE_URL).rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "mode": "live-read-only" if "live.trading212.com" in self.base_url else "demo-read-only",
            "base_url": self.base_url,
            "can_trade": False,
            "message": "Trading 212 API key 已配置，当前只读，不会自动交易。"
            if self.configured
            else "未配置 Trading 212 API key，将使用手动持仓录入。",
        }

    def account(self) -> dict[str, Any]:
        cash = self._get("/equity/account/cash")
        info = self._try_get("/equity/account/info")
        return {"cash": cash, "info": info}

    def positions(self) -> list[dict[str, Any]]:
        return self._get("/equity/portfolio")

    def _try_get(self, path: str) -> Any | None:
        try:
            return self._get(path)
        except Trading212Error as exc:
            logger.info("Trading 212 optional endpoint failed path=%s status=%s", path, exc.status_code)
            return None

    def _get(self, path: str) -> Any:
        if not self.configured:
            raise Trading212Error("未配置 Trading 212 API Key/API Secret。", status_code=503)
        url = f"{self.base_url}{path}"
        try:
            response = requests.get(url, auth=(self.api_key, self.api_secret), timeout=self.timeout)
        except requests.Timeout as exc:
            raise Trading212Error("Trading 212 请求超时，请稍后重试。", status_code=408) from exc
        except requests.RequestException as exc:
            raise Trading212Error("Trading 212 连接失败，请检查网络或 Render 出站访问。", status_code=502) from exc

        if response.status_code == 401:
            raise Trading212Error("Trading 212 API 密钥无效或已过期。", status_code=401)
        if response.status_code == 403:
            raise Trading212Error("Trading 212 拒绝访问，请确认账户类型和 API 权限。", status_code=403)
        if response.status_code == 408:
            raise Trading212Error("Trading 212 请求超时，请稍后重试。", status_code=408)
        if response.status_code == 429:
            raise Trading212Error("Trading 212 接口限流，请等待几秒后刷新。", status_code=429)
        if response.status_code >= 400:
            raise Trading212Error("Trading 212 返回错误，请稍后重试。", status_code=response.status_code)
        return response.json()

