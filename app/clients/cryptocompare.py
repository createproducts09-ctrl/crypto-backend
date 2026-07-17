from __future__ import annotations

from typing import Any

import httpx

from app.config import Config


class CryptoCompareClient:
    def __init__(self):
        self.base_url = "https://min-api.cryptocompare.com/data"
        self.api_key = Config.CRYPTOCOMPARE_API_KEY

    def latest_news(self, limit: int = 50, categories: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"lang": "EN"}
        if categories:
            params["categories"] = categories
        headers = {}
        if self.api_key:
            headers["authorization"] = f"Apikey {self.api_key}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{self.base_url}/v2/news/", params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("Data") or []
            return items[:limit]


cryptocompare = CryptoCompareClient()
