from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# CoinGecko id → DefiLlama slug (protocols or chains)
COIN_TO_LLAMA: dict[str, dict[str, str]] = {
    "bitcoin": {"type": "chain", "slug": "Bitcoin"},
    "ethereum": {"type": "chain", "slug": "Ethereum"},
    "solana": {"type": "chain", "slug": "Solana"},
    "sui": {"type": "chain", "slug": "Sui"},
    "avalanche-2": {"type": "chain", "slug": "Avalanche"},
    "cardano": {"type": "chain", "slug": "Cardano"},
    "polkadot": {"type": "chain", "slug": "Polkadot"},
    "near": {"type": "chain", "slug": "Near"},
    "aptos": {"type": "chain", "slug": "Aptos"},
    "cosmos": {"type": "chain", "slug": "Cosmos"},
    "tron": {"type": "chain", "slug": "Tron"},
    "binancecoin": {"type": "chain", "slug": "BSC"},
    "matic-network": {"type": "chain", "slug": "Polygon"},
    "polygon-ecosystem-token": {"type": "chain", "slug": "Polygon"},
    "arbitrum": {"type": "chain", "slug": "Arbitrum"},
    "optimism": {"type": "chain", "slug": "Optimism"},
    "base": {"type": "chain", "slug": "Base"},
    "aave": {"type": "protocol", "slug": "aave"},
    "uniswap": {"type": "protocol", "slug": "uniswap"},
    "chainlink": {"type": "protocol", "slug": "chainlink"},
    "lido-dao": {"type": "protocol", "slug": "lido"},
    "maker": {"type": "protocol", "slug": "makerdao"},
    "curve-dao-token": {"type": "protocol", "slug": "curve-finance"},
    "render-token": {"type": "protocol", "slug": "render"},
    "the-graph": {"type": "protocol", "slug": "the-graph"},
    "hyperliquid": {"type": "protocol", "slug": "hyperliquid"},
    "bittensor": {"type": "protocol", "slug": "bittensor"},
}


class DefiLlamaClient:
    """Lightweight DefiLlama client for TVL signals."""

    def __init__(self):
        self.base = "https://api.llama.fi"
        self._protocols_cache: list[dict] | None = None
        self._protocols_at = 0.0
        self._chains_cache: list[dict] | None = None
        self._chains_at = 0.0

    def _get(self, path: str) -> Any:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{self.base}{path}")
            resp.raise_for_status()
            return resp.json()

    def protocols(self) -> list[dict]:
        now = time.time()
        if self._protocols_cache and now - self._protocols_at < 3600:
            return self._protocols_cache
        try:
            data = self._get("/protocols")
            self._protocols_cache = data if isinstance(data, list) else []
            self._protocols_at = now
        except Exception as exc:
            log.warning("DefiLlama protocols failed: %s", exc)
            return self._protocols_cache or []
        return self._protocols_cache

    def chains(self) -> list[dict]:
        now = time.time()
        if self._chains_cache and now - self._chains_at < 3600:
            return self._chains_cache
        try:
            data = self._get("/v2/chains")
            self._chains_cache = data if isinstance(data, list) else []
            self._chains_at = now
        except Exception as exc:
            log.warning("DefiLlama chains failed: %s", exc)
            return self._chains_cache or []
        return self._chains_cache

    def tvl_for_coin(self, coin_id: str) -> dict[str, Any] | None:
        """Return {tvl, change_1d, change_7d, source, slug} or None."""
        mapping = COIN_TO_LLAMA.get(coin_id)
        if not mapping:
            # Try protocol list by gecko_id
            for p in self.protocols():
                if (p.get("gecko_id") or "").lower() == coin_id.lower():
                    return {
                        "tvl": p.get("tvl"),
                        "change_1d": p.get("change_1d"),
                        "change_7d": p.get("change_7d"),
                        "source": "DefiLlama",
                        "slug": p.get("slug") or p.get("name"),
                        "kind": "protocol",
                    }
            return None

        kind = mapping["type"]
        slug = mapping["slug"]
        if kind == "chain":
            for c in self.chains():
                if (c.get("name") or "").lower() == slug.lower() or (c.get("gecko_id") or "") == coin_id:
                    tvl = c.get("tvl")
                    return {
                        "tvl": tvl,
                        "change_1d": None,
                        "change_7d": None,
                        "source": "DefiLlama",
                        "slug": c.get("name") or slug,
                        "kind": "chain",
                    }
        else:
            for p in self.protocols():
                if (p.get("slug") or "").lower() == slug.lower() or (p.get("gecko_id") or "") == coin_id:
                    return {
                        "tvl": p.get("tvl"),
                        "change_1d": p.get("change_1d"),
                        "change_7d": p.get("change_7d"),
                        "source": "DefiLlama",
                        "slug": p.get("slug") or slug,
                        "kind": "protocol",
                    }
        return None


defillama = DefiLlamaClient()
