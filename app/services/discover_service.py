from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import Any

from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.clients.coingecko import coingecko
from app.extensions import db
from app.services.coin_service import ensure_seed_markets, upsert_markets
from app.utils.scoring import map_market_coin


CATEGORY_MAP = {
    "defi": "decentralized-finance-defi",
    "ai": "artificial-intelligence",
    "gaming": "gaming",
    "layer-1": "layer-1",
    "layer-2": "layer-2",
    "meme": "meme-token",
    "rwa": "real-world-assets-rwa",
    "infrastructure": "infrastructure",
}


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _optional_user_id() -> str | None:
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:
        return None


def _seeded_shuffle(items: list[dict], seed: str) -> list[dict]:
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    out = list(items)
    rng.shuffle(out)
    return out


def _candidate_pool(filter_key: str | None = None) -> list[dict[str, Any]]:
    ensure_seed_markets()
    coins = list(db.coins.find({}, {"_id": 0}).sort("market_cap_rank", 1).limit(250))

    key = (filter_key or "trending").lower()
    if key in {"gainers", "biggest_gainers"}:
        coins.sort(key=lambda c: c.get("price_change_percentage_24h") or -999, reverse=True)
    elif key in {"losers", "biggest_losers"}:
        coins.sort(key=lambda c: c.get("price_change_percentage_24h") or 999)
    elif key in {"low_mcap", "low_market_cap"}:
        coins = [c for c in coins if (c.get("market_cap") or 0) < 500_000_000]
        coins.sort(key=lambda c: c.get("market_cap") or 0)
    elif key in {"high_volume"}:
        coins.sort(key=lambda c: c.get("total_volume") or 0, reverse=True)
    elif key in {"ai_picks", "hidden_gems"}:
        coins.sort(
            key=lambda c: (c.get("community_score") or 0) + (c.get("liquidity_score") or 0),
            reverse=True,
        )
    elif key == "new_listings":
        coins.sort(key=lambda c: c.get("market_cap_rank") or 9999, reverse=True)
    elif key in CATEGORY_MAP:
        try:
            markets = coingecko.markets(page=1, per_page=80, category=CATEGORY_MAP[key])
            upsert_markets(markets)
            coins = [map_market_coin(m) for m in markets]
            for c in coins:
                cached = db.coins.find_one(
                    {"id": c["id"]},
                    {"ai_insight": 1, "tags": 1, "sentiment": 1, "risk": 1, "community_score": 1},
                )
                if cached:
                    c.update({k: cached[k] for k in cached if k != "_id"})
        except Exception:
            coins = [c for c in coins if key.replace("-", " ") in " ".join(c.get("tags") or []).lower()]
    else:
        try:
            trending = coingecko.trending()
            ids = [t["item"]["id"] for t in trending.get("coins", []) if t.get("item")]
            ranked = {cid: i for i, cid in enumerate(ids)}
            coins.sort(key=lambda c: ranked.get(c.get("id"), 999))
        except Exception:
            pass

    return coins


def _user_sets(user_id: str) -> tuple[set[str], set[str], set[str]]:
    """Returns (passed, interested, seen_today)."""
    today = _today_key()
    passed: set[str] = set()
    interested: set[str] = set()
    seen_today: set[str] = set()
    for doc in db.discover_swipes.find({"user_id": user_id}):
        cid = doc.get("coin_id")
        if not cid:
            continue
        action = doc.get("action")
        if action == "pass":
            passed.add(cid)
        elif action in {"interested", "watch"}:
            interested.add(cid)
        day = doc.get("day_key") or (
            doc.get("created_at").strftime("%Y-%m-%d") if doc.get("created_at") else None
        )
        if day == today:
            seen_today.add(cid)
    return passed, interested, seen_today


def get_deck(
    filter_key: str | None = None,
    limit: int = 30,
    user_id: str | None = None,
    exclude_ids: list[str] | None = None,
    allow_recycle: bool = False,
) -> dict[str, Any]:
    """
    Unique daily recommendations:
    - Exclude passed coins permanently (until recycle)
    - Exclude already shown today (fresh every day)
    - Seeded shuffle so order is unique per user/day/filter
    - Recycle oldest passes only when fresh pool is empty / allow_recycle
    """
    limit = max(1, min(int(limit or 30), 50))
    pool = _candidate_pool(filter_key)
    today = _today_key()

    passed: set[str] = set()
    interested: set[str] = set()
    seen_today: set[str] = set()
    if user_id:
        passed, interested, seen_today = _user_sets(user_id)
    if exclude_ids:
        passed |= {str(x) for x in exclude_ids if x}

    blocked = passed | seen_today
    # Prefer not re-showing interested immediately today either
    blocked |= interested & seen_today

    fresh = [c for c in pool if c.get("id") and c["id"] not in blocked]
    recycled_used = False

    if len(fresh) < max(5, limit // 2) and (allow_recycle or len(fresh) == 0):
        # Rare recycle: oldest passed coins first
        recycled_used = True
        recycle_ids: list[str] = []
        if user_id:
            cursor = db.discover_swipes.find(
                {"user_id": user_id, "action": "pass"}
            ).sort("created_at", 1)
            recycle_ids = [d["coin_id"] for d in cursor if d.get("coin_id")]
        else:
            recycle_ids = list(passed)

        by_id = {c["id"]: c for c in pool if c.get("id")}
        for cid in recycle_ids:
            if cid in seen_today:
                continue
            coin = by_id.get(cid)
            if coin and coin not in fresh:
                fresh.append(coin)
            if len(fresh) >= limit * 2:
                break

    seed = f"{today}:{filter_key or 'trending'}:{user_id or 'guest'}"
    shuffled = _seeded_shuffle(fresh, seed)
    items = shuffled[:limit]

    return {
        "items": items,
        "meta": {
            "day": today,
            "filter": filter_key or "trending",
            "fresh_remaining": len(fresh),
            "passed_count": len(passed),
            "seen_today": len(seen_today),
            "recycled": recycled_used,
            "unique": True,
        },
    }


def record_swipe(user_id: str, coin_id: str, action: str) -> dict[str, Any]:
    coin_id = (coin_id or "").strip()
    action = (action or "").strip().lower()
    if not coin_id:
        raise ValueError("coin_id required")
    if action not in {"pass", "interested", "watch"}:
        raise ValueError("action must be pass, interested, or watch")

    now = datetime.now(timezone.utc)
    day = _today_key()
    db.discover_swipes.update_one(
        {"user_id": user_id, "coin_id": coin_id},
        {
            "$set": {
                "action": action,
                "day_key": day,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    if action == "pass":
        try:
            from app.services import conviction_service

            conviction_service.close_open_for_coin(user_id, coin_id, reason="passed")
        except Exception:
            pass
    return {"ok": True, "coin_id": coin_id, "action": action, "day": day}


def allow_passed_again(user_id: str, coin_ids: list[str] | None = None) -> dict[str, Any]:
    """User explicitly allows rejected coins back into the deck."""
    query: dict[str, Any] = {"user_id": user_id, "action": "pass"}
    if coin_ids:
        query["coin_id"] = {"$in": [str(c) for c in coin_ids if c]}
    res = db.discover_swipes.delete_many(query)
    return {"ok": True, "cleared": res.deleted_count}


def get_stats(user_id: str) -> dict[str, Any]:
    passed, interested, seen_today = _user_sets(user_id)
    return {
        "day": _today_key(),
        "passed_count": len(passed),
        "interested_count": len(interested),
        "seen_today": len(seen_today),
        "passed": sorted(passed),
        "interested": sorted(interested),
        "seen_today_ids": sorted(seen_today),
    }


def list_filters() -> list[dict[str, str]]:
    return [
        {"key": "trending", "label": "Trending"},
        {"key": "new_listings", "label": "New Listings"},
        {"key": "gainers", "label": "Biggest Gainers"},
        {"key": "losers", "label": "Biggest Losers"},
        {"key": "ai_picks", "label": "AI Picks"},
        {"key": "hidden_gems", "label": "Hidden Gems"},
        {"key": "low_mcap", "label": "Low Market Cap"},
        {"key": "high_volume", "label": "High Volume"},
        {"key": "meme", "label": "Meme Coins"},
        {"key": "defi", "label": "DeFi"},
        {"key": "ai", "label": "AI Tokens"},
        {"key": "gaming", "label": "Gaming"},
        {"key": "infrastructure", "label": "Infrastructure"},
        {"key": "layer-1", "label": "Layer 1"},
        {"key": "layer-2", "label": "Layer 2"},
        {"key": "rwa", "label": "RWA"},
    ]
