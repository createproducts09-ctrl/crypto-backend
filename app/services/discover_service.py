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

# Used when CoinGecko category fetch fails — match against local categories/tags.
CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "defi": ("defi", "decentralized finance", "decentralized-finance-defi", "yield"),
    "ai": ("artificial intelligence", "ai", "artificial-intelligence", "machine learning"),
    "gaming": ("gaming", "gamefi", "play to earn", "metaverse"),
    "layer-1": ("layer 1", "layer-1", "l1"),
    "layer-2": ("layer 2", "layer-2", "l2", "scaling"),
    "meme": ("meme", "meme-token", "memes", "dog coin"),
    "rwa": ("real-world assets", "rwa", "real world assets", "real-world-assets-rwa"),
    "infrastructure": ("infrastructure", "oracle", "data availability", "interoperability"),
}

# Preserve sorted order for these — do not reshuffle the whole pool.
RANKED_FILTERS = {
    "trending",
    "gainers",
    "biggest_gainers",
    "losers",
    "biggest_losers",
    "low_mcap",
    "low_market_cap",
    "high_volume",
    "ai_picks",
    "hidden_gems",
    "new_listings",
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


def _change_24h(coin: dict[str, Any]) -> float:
    try:
        return float(coin.get("price_change_percentage_24h") or 0)
    except (TypeError, ValueError):
        return 0.0


def _mcap(coin: dict[str, Any]) -> float:
    try:
        return float(coin.get("market_cap") or 0)
    except (TypeError, ValueError):
        return 0.0


def _volume(coin: dict[str, Any]) -> float:
    try:
        return float(coin.get("total_volume") or 0)
    except (TypeError, ValueError):
        return 0.0


def _rank(coin: dict[str, Any]) -> int:
    try:
        return int(coin.get("market_cap_rank") or 99999)
    except (TypeError, ValueError):
        return 99999


def _score(coin: dict[str, Any]) -> float:
    try:
        return float(coin.get("community_score") or 0) + float(
            coin.get("liquidity_score") or 0
        )
    except (TypeError, ValueError):
        return 0.0


def _category_blob(coin: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("categories", "tags"):
        val = coin.get(key) or []
        if isinstance(val, list):
            parts.extend(str(x) for x in val)
        elif val:
            parts.append(str(val))
    name = f"{coin.get('name') or ''} {coin.get('id') or ''}"
    parts.append(name)
    return " ".join(parts).lower()


def _match_category(coin: dict[str, Any], filter_key: str) -> bool:
    blob = _category_blob(coin)
    aliases = CATEGORY_ALIASES.get(filter_key, (filter_key.replace("-", " "),))
    return any(alias in blob for alias in aliases)


def _load_base_coins(limit: int = 400) -> list[dict[str, Any]]:
    ensure_seed_markets()
    return list(db.coins.find({}, {"_id": 0}).sort("market_cap_rank", 1).limit(limit))


def _candidate_pool(filter_key: str | None = None) -> list[dict[str, Any]]:
    coins = _load_base_coins()
    key = (filter_key or "trending").lower()

    if key in {"gainers", "biggest_gainers"}:
        coins = [c for c in coins if _change_24h(c) > 0.5]
        coins.sort(key=_change_24h, reverse=True)
        return coins

    if key in {"losers", "biggest_losers"}:
        coins = [c for c in coins if _change_24h(c) < -0.5]
        coins.sort(key=_change_24h)  # most negative first
        return coins

    if key in {"low_mcap", "low_market_cap"}:
        coins = [c for c in coins if 1_000_000 <= _mcap(c) < 500_000_000]
        coins.sort(key=_mcap)
        return coins

    if key == "high_volume":
        coins = [c for c in coins if _volume(c) > 0]
        coins.sort(key=_volume, reverse=True)
        return coins

    if key == "ai_picks":
        # Momentum + desk scores — distinct from hidden gems.
        coins = [
            c
            for c in coins
            if _score(c) > 0 and (_change_24h(c) > -8 or (c.get("sentiment") == "bullish"))
        ]
        coins.sort(
            key=lambda c: (
                _score(c),
                _change_24h(c),
                _volume(c),
            ),
            reverse=True,
        )
        return coins

    if key == "hidden_gems":
        coins = [
            c
            for c in coins
            if 5_000_000 <= _mcap(c) <= 1_000_000_000 and _rank(c) >= 40
        ]
        coins.sort(
            key=lambda c: (_score(c), -_mcap(c) if _mcap(c) else 0),
            reverse=True,
        )
        return coins

    if key == "new_listings":
        # Proxy for recently listed / lower-cap names: real ranks only, farther down the board.
        coins = [
            c
            for c in coins
            if isinstance(c.get("market_cap_rank"), int) and int(c["market_cap_rank"]) >= 100
        ]
        coins.sort(key=_rank, reverse=True)
        return coins

    if key in CATEGORY_MAP:
        try:
            markets = coingecko.markets(
                page=1, per_page=100, category=CATEGORY_MAP[key]
            )
            if markets:
                # Persist market tape only — never generate Gemini insights here.
                upsert_markets(markets, generate_insights=False)
                mapped = [map_market_coin(m) for m in markets]
                for c in mapped:
                    cached = db.coins.find_one({"id": c["id"]}, {"_id": 0})
                    if cached:
                        for field in (
                            "ai_insight",
                            "tags",
                            "categories",
                            "sentiment",
                            "risk",
                            "community_score",
                            "liquidity_score",
                        ):
                            if cached.get(field) is not None:
                                c[field] = cached[field]
                return [c for c in mapped if c.get("id")]
        except Exception:
            pass
        matched = [c for c in coins if _match_category(c, key)]
        matched.sort(key=_mcap, reverse=True)
        return matched

    # Trending (default)
    try:
        trending = coingecko.trending()
        ids = [t["item"]["id"] for t in trending.get("coins", []) if t.get("item")]
        if ids:
            ranked = {cid: i for i, cid in enumerate(ids)}
            # Pull any trending coins missing from the local top slice.
            missing = [cid for cid in ids if cid not in {c.get("id") for c in coins}]
            if missing:
                extra = list(
                    db.coins.find({"id": {"$in": missing}}, {"_id": 0})
                )
                coins = coins + extra
            trending_coins = [c for c in coins if c.get("id") in ranked]
            trending_coins.sort(key=lambda c: ranked.get(c.get("id"), 999))
            # Fill remainder with top market-cap names not already included.
            seen = {c.get("id") for c in trending_coins}
            rest = [c for c in coins if c.get("id") not in seen]
            rest.sort(key=_rank)
            return trending_coins + rest
    except Exception:
        pass

    coins.sort(key=_rank)
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
    include_why: bool = False,
) -> dict[str, Any]:
    """
    Filtered recommendations:
    - Ranked filters keep sort order (gainers stay gainers)
    - Categories may lightly shuffle within the matched set
    - Swipe exclusions still apply when user_id is set
    """
    limit = max(1, min(int(limit or 30), 50))
    key = (filter_key or "trending").lower()
    pool = _candidate_pool(key)
    today = _today_key()

    passed: set[str] = set()
    interested: set[str] = set()
    seen_today: set[str] = set()
    if user_id:
        passed, interested, seen_today = _user_sets(user_id)
    if exclude_ids:
        passed |= {str(x) for x in exclude_ids if x}

    blocked = passed | seen_today
    blocked |= interested & seen_today

    fresh = [c for c in pool if c.get("id") and c["id"] not in blocked]
    recycled_used = False

    if len(fresh) < max(5, limit // 2) and (allow_recycle or len(fresh) == 0):
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

    seed = f"{today}:{key}:{user_id or 'guest'}"
    if key in RANKED_FILTERS or key == "trending":
        # Keep filter ranking intact so chips actually feel different.
        items = fresh[:limit]
    else:
        # Category / explore: light shuffle for variety inside the matched set.
        items = _seeded_shuffle(fresh, seed)[:limit]

    if include_why:
        from app.services.billing_service import why_blurb

        enriched = []
        for coin in items:
            row = dict(coin)
            row["why_blurb"] = why_blurb(coin)
            enriched.append(row)
        items = enriched

    return {
        "items": items,
        "meta": {
            "day": today,
            "filter": key,
            "fresh_remaining": len(fresh),
            "passed_count": len(passed),
            "seen_today": len(seen_today),
            "recycled": recycled_used,
            "unique": True,
            "why_blurbs": include_why,
            "pool_size": len(pool),
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
    return {"ok": True, "coin_id": coin_id, "action": action, "day": day}


def allow_passed_again(user_id: str, coin_ids: list[str] | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {"user_id": user_id, "action": "pass"}
    if coin_ids:
        query["coin_id"] = {"$in": [str(x) for x in coin_ids if x]}
    result = db.discover_swipes.delete_many(query)
    return {"ok": True, "cleared": result.deleted_count}


def get_stats(user_id: str) -> dict[str, Any]:
    passed, interested, seen_today = _user_sets(user_id)
    return {
        "passed_count": len(passed),
        "interested_count": len(interested),
        "seen_today": len(seen_today),
        "passed": sorted(passed),
        "interested": sorted(interested),
        "seen_today_ids": sorted(seen_today),
    }


def list_filters(user_id: str | None = None) -> list[dict]:
    from app.services.billing_service import FREE_FILTERS, is_keel

    keel = bool(user_id and is_keel(user_id))
    raw = [
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
    out = []
    for f in raw:
        locked = not keel and f["key"] not in FREE_FILTERS
        out.append(
            {
                **f,
                "tier": "free" if f["key"] in FREE_FILTERS else "keel",
                "locked": locked,
            }
        )
    return out
