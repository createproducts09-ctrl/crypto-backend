from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.clients.ai import ai_service
from app.clients.coingecko import coingecko
from app.extensions import db
from app.utils.indicators import bollinger, ema, macd, rsi, summarize_ta
from app.utils.research_copy import build_fundamentals, build_technical_takeaways, clean_prose, split_sentences
from app.utils.scoring import map_market_coin

logger = logging.getLogger(__name__)


TIMEFRAME_DAYS = {
    "1H": "1",
    "24H": "1",
    "7D": "7",
    "30D": "30",
    "3M": "90",
    "1Y": "365",
    "ALL": "max",
}


def upsert_markets(raw_markets: list[dict[str, Any]]) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for raw in raw_markets:
        mapped = map_market_coin(raw)
        if not mapped.get("ai_insight"):
            mapped["ai_insight"] = ai_service.insight_for_coin(mapped)
        mapped["updated_at"] = now
        db.coins.update_one({"id": mapped["id"]}, {"$set": mapped}, upsert=True)
        count += 1
    return count


def list_cached_coins(limit: int = 100, skip: int = 0) -> list[dict]:
    cursor = db.coins.find({}, {"_id": 0}).sort("market_cap_rank", 1).skip(skip).limit(limit)
    return list(cursor)


def _refresh_coin_async(coin_id: str):
    """Refresh coin data from CoinGecko in background."""
    try:
        detail = coingecko.coin_detail(coin_id)
        md = detail.get("market_data") or {}
        links = detail.get("links") or {}
        description = clean_prose(((detail.get("description") or {}).get("en") or ""))[:2000]
        categories = detail.get("categories") or []
        merged = {
            "id": detail.get("id"),
            "symbol": (detail.get("symbol") or "").upper(),
            "name": detail.get("name"),
            "image": (detail.get("image") or {}).get("large"),
            "description": description,
            "about_bullets": split_sentences(description, limit=5) or [
                f"{detail.get('name') or coin_id} is a digital asset we track for calm research.",
                "Open Fundamentals and AI Brief for structured bullets instead of a wall of text.",
            ],
            "genesis_date": detail.get("genesis_date"),
            "hashing_algorithm": detail.get("hashing_algorithm"),
            "categories": categories,
            "tags": categories[:6],
            "homepage": (links.get("homepage") or [None])[0],
            "current_price": (md.get("current_price") or {}).get("usd"),
            "market_cap": (md.get("market_cap") or {}).get("usd"),
            "fully_diluted_valuation": (md.get("fully_diluted_valuation") or {}).get("usd"),
            "total_volume": (md.get("total_volume") or {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "max_supply": md.get("max_supply"),
            "price_change_percentage_24h": md.get("price_change_percentage_24h"),
            "price_change_percentage_7d": md.get("price_change_percentage_7d"),
            "price_change_percentage_30d": md.get("price_change_percentage_30d"),
            "ath": (md.get("ath") or {}).get("usd"),
            "atl": (md.get("atl") or {}).get("usd"),
            "market_cap_rank": detail.get("market_cap_rank"),
            "community_data": detail.get("community_data"),
            "developer_data": detail.get("developer_data"),
            "updated_at": datetime.now(timezone.utc),
        }
        if not merged.get("ai_insight"):
            merged["ai_insight"] = ai_service.insight_for_coin(merged)
        merged["fundamentals"] = build_fundamentals(merged, description, categories)
        db.coins.update_one({"id": coin_id}, {"$set": merged}, upsert=True)
    except Exception:
        pass


def get_coin(coin_id: str) -> dict | None:
    try:
        cached = db.coins.find_one({"id": coin_id}, {"_id": 0})
        if cached:
            # Refresh in background if cache is stale (older than 5 minutes)
            if cached.get("updated_at"):
                age = datetime.now(timezone.utc) - cached["updated_at"]
                if age.total_seconds() > 300:
                    thread = threading.Thread(target=_refresh_coin_async, args=(coin_id,), daemon=True)
                    thread.start()
            return cached
        # No cache, fetch synchronously
        try:
            detail = coingecko.coin_detail(coin_id)
        except Exception:
            return None

        md = detail.get("market_data") or {}
        links = detail.get("links") or {}
        description = clean_prose(((detail.get("description") or {}).get("en") or ""))[:2000]
        categories = detail.get("categories") or []
        merged = {
            "id": detail.get("id"),
            "symbol": (detail.get("symbol") or "").upper(),
            "name": detail.get("name"),
            "image": (detail.get("image") or {}).get("large"),
            "description": description,
            "about_bullets": split_sentences(description, limit=5) or [
                f"{detail.get('name') or coin_id} is a digital asset we track for calm research.",
                "Open Fundamentals and AI Brief for structured bullets instead of a wall of text.",
            ],
            "genesis_date": detail.get("genesis_date"),
            "hashing_algorithm": detail.get("hashing_algorithm"),
            "categories": categories,
            "tags": categories[:6],
            "homepage": (links.get("homepage") or [None])[0],
            "current_price": (md.get("current_price") or {}).get("usd"),
            "market_cap": (md.get("market_cap") or {}).get("usd"),
            "fully_diluted_valuation": (md.get("fully_diluted_valuation") or {}).get("usd"),
            "total_volume": (md.get("total_volume") or {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "max_supply": md.get("max_supply"),
            "price_change_percentage_24h": md.get("price_change_percentage_24h"),
            "price_change_percentage_7d": md.get("price_change_percentage_7d"),
            "price_change_percentage_30d": md.get("price_change_percentage_30d"),
            "ath": (md.get("ath") or {}).get("usd"),
            "atl": (md.get("atl") or {}).get("usd"),
            "market_cap_rank": detail.get("market_cap_rank"),
            "community_data": detail.get("community_data"),
            "developer_data": detail.get("developer_data"),
            "updated_at": datetime.now(timezone.utc),
        }
        if not merged.get("ai_insight"):
            merged["ai_insight"] = ai_service.insight_for_coin(merged)
        merged["fundamentals"] = build_fundamentals(merged, description, categories)
        db.coins.update_one({"id": coin_id}, {"$set": merged}, upsert=True)
        return merged
    except Exception as exc:
        logger.error("Error fetching coin %s: %s", coin_id, exc)
        return None


def get_chart(coin_id: str, timeframe: str = "7D") -> dict[str, Any]:
    days = TIMEFRAME_DAYS.get(timeframe.upper(), "7")
    raw = coingecko.market_chart(coin_id, days=days)
    prices = raw.get("prices") or []
    volumes = raw.get("total_volumes") or []
    closes = [p[1] for p in prices]
    timestamps = [p[0] for p in prices]
    ta = summarize_ta(closes)
    indicators = {
        "ema_20": ema(closes, 20),
        "ema_50": ema(closes, 50),
        "rsi": rsi(closes),
        "macd": macd(closes),
        "bollinger": bollinger(closes),
    }
    # Trim bulky null-heavy series for response size: send last 200 points
    def trim(series):
        if isinstance(series, dict):
            return {k: trim(v) for k, v in series.items()}
        if isinstance(series, list):
            return series[-200:]
        return series

    research = ai_service.research_summary(
        db.coins.find_one({"id": coin_id}, {"_id": 0}) or {"id": coin_id, "name": coin_id},
        ta,
    )
    return {
        "timeframe": timeframe.upper(),
        "timestamps": timestamps[-200:],
        "prices": closes[-200:],
        "volumes": [v[1] for v in volumes][-200:],
        "indicators": trim(indicators),
        "technical_summary": ta,
        "technical_takeaways": build_technical_takeaways(
            ta, db.coins.find_one({"id": coin_id}, {"_id": 0}) or {"id": coin_id, "name": coin_id}
        ),
        "ai_research": research,
    }


def _seed_markets_async():
    """Seed markets from CoinGecko in background."""
    try:
        markets = coingecko.markets(page=1, per_page=100)
        upsert_markets(markets)
    except Exception:
        pass


def ensure_seed_markets() -> list[dict]:
    existing = list_cached_coins(limit=5)
    if existing:
        return list_cached_coins(limit=100)
    # Seed in background, return empty list for now
    thread = threading.Thread(target=_seed_markets_async, daemon=True)
    thread.start()
    return []
