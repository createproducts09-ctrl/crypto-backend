from __future__ import annotations

from typing import Any


def sentiment_from_change(change_24h: float | None, change_7d: float | None = None) -> str:
    c24 = change_24h or 0
    c7 = change_7d or 0
    score = c24 * 0.6 + c7 * 0.4
    if score > 3:
        return "bullish"
    if score < -3:
        return "bearish"
    return "neutral"


def risk_score(market_cap: float | None, volume: float | None, change_24h: float | None) -> dict[str, Any]:
    mcap = market_cap or 0
    vol = volume or 0
    ch = abs(change_24h or 0)
    if mcap > 10_000_000_000 and ch < 8:
        level = "low"
        confidence = 0.78
    elif mcap > 1_000_000_000 and ch < 15:
        level = "medium"
        confidence = 0.7
    else:
        level = "high"
        confidence = 0.65 if mcap > 0 else 0.4
    if vol and mcap and vol / mcap > 0.35:
        level = "high" if level != "high" else level
        confidence = min(0.9, confidence + 0.05)
    return {"level": level, "confidence": confidence}


def community_score(coin: dict[str, Any]) -> float:
    # Heuristic 0-100 from available social/volume proxies
    base = 50.0
    change = coin.get("price_change_percentage_24h") or 0
    base += max(-15, min(15, change))
    rank = coin.get("market_cap_rank") or 200
    base += max(0, 25 - (rank / 8))
    return round(max(0, min(100, base)), 1)


def map_market_coin(raw: dict[str, Any], ai_insight: str | None = None) -> dict[str, Any]:
    spark = (raw.get("sparkline_in_7d") or {}).get("price") or []
    change_7d = raw.get("price_change_percentage_7d_in_currency")
    change_24h = raw.get("price_change_percentage_24h")
    tags = []
    # Categories come later from detail; keep placeholders from symbol heuristics
    symbol = (raw.get("symbol") or "").upper()
    if symbol in {"BTC", "ETH"}:
        tags.append("Layer 1")
    return {
        "id": raw.get("id"),
        "symbol": symbol,
        "name": raw.get("name"),
        "image": raw.get("image"),
        "current_price": raw.get("current_price"),
        "market_cap": raw.get("market_cap"),
        "market_cap_rank": raw.get("market_cap_rank"),
        "fully_diluted_valuation": raw.get("fully_diluted_valuation"),
        "total_volume": raw.get("total_volume"),
        "circulating_supply": raw.get("circulating_supply"),
        "total_supply": raw.get("total_supply"),
        "max_supply": raw.get("max_supply"),
        "ath": raw.get("ath"),
        "atl": raw.get("atl"),
        "price_change_percentage_1h": raw.get("price_change_percentage_1h_in_currency"),
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d": change_7d,
        "price_change_percentage_30d": raw.get("price_change_percentage_30d_in_currency"),
        "sparkline": spark[-48:] if spark else [],
        "sentiment": sentiment_from_change(change_24h, change_7d),
        "risk": risk_score(raw.get("market_cap"), raw.get("total_volume"), change_24h),
        "community_score": community_score(raw),
        "liquidity_score": round(min(100, ((raw.get("total_volume") or 0) / max(raw.get("market_cap") or 1, 1)) * 400), 1),
        "tags": tags,
        "ai_insight": ai_insight,
        "last_updated": raw.get("last_updated"),
    }
