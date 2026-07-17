from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import Config


class CoinGeckoClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or Config.COINGECKO_BASE_URL).rstrip("/")
        self._last_call = 0.0
        self._backoff_until = 0.0

    def _throttle(self):
        now = time.time()
        if now < self._backoff_until:
            time.sleep(self._backoff_until - now)
        elapsed = time.time() - self._last_call
        # Free tier is strict — keep calls spaced out
        if elapsed < 2.5:
            time.sleep(2.5 - elapsed)
        self._last_call = time.time()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._throttle()
        with httpx.Client(timeout=30.0) as client:
            for attempt in range(3):
                resp = client.get(f"{self.base_url}{path}", params=params or {})
                if resp.status_code == 429:
                    wait = 8 * (attempt + 1)
                    self._backoff_until = time.time() + wait
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            resp.raise_for_status()
            return resp.json()

    def markets(self, page: int = 1, per_page: int = 100, category: str | None = None) -> list[dict]:
        params: dict[str, Any] = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "true",
            "price_change_percentage": "1h,24h,7d,30d",
        }
        if category:
            params["category"] = category
        return self._get("/coins/markets", params)

    def coin_detail(self, coin_id: str) -> dict:
        return self._get(
            f"/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "true",
                "sparkline": "true",
            },
        )

    def market_chart(self, coin_id: str, days: str = "7") -> dict:
        return self._get(
            f"/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days},
        )

    def trending(self) -> dict:
        return self._get("/search/trending")

    def search(self, query: str) -> dict:
        return self._get("/search", {"query": query})

    def categories(self) -> list[dict]:
        return self._get("/coins/categories/list")


coingecko = CoinGeckoClient()
