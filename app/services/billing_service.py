"""MVP billing / entitlements — free vs keel (mock upgrade, no Stripe yet)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.extensions import db

PLANS = {
    "free": {
        "id": "free",
        "name": "Free",
        "price_monthly": 0,
        "price_yearly": 0,
        "tagline": "Start researching",
        "features": [
            "Discover — core filters",
            "5 Ask AI messages / day",
            "1 portfolio basket",
            "Personal watchlist",
        ],
    },
    "keel": {
        "id": "keel",
        "name": "Keel",
        "price_monthly": 4.99,
        "price_yearly": 29,
        "tagline": "Unlimited research desk",
        "features": [
            "All Discover filters",
            "Unlimited Ask AI",
            "Unlimited baskets",
            "“Why this coin?” on every swipe",
            "Swipe Pulse — crowd passes, likes & watchlists",
            "Priority research tools",
        ],
    },
}

# Free-tier Discover filter keys
FREE_FILTERS = {"trending", "gainers", "losers", "new_listings"}

FREE_AI_PER_DAY = 5
FREE_BASKET_LIMIT = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_key() -> str:
    return _now().strftime("%Y-%m-%d")


def normalize_plan(plan: str | None) -> str:
    p = (plan or "free").strip().lower()
    return p if p in PLANS else "free"


def get_user_plan(user_id: str) -> str:
    user = db.users.find_one({"_id": ObjectId(user_id)}, {"plan": 1})
    if not user:
        return "free"
    return normalize_plan(user.get("plan"))


def is_keel(user_id: str | None) -> bool:
    if not user_id:
        return False
    return get_user_plan(user_id) == "keel"


def list_plans() -> list[dict[str, Any]]:
    return list(PLANS.values())


def ai_messages_today(user_id: str) -> int:
    """Count user-role AI messages sent today across their threads."""
    start = datetime.strptime(_today_key(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    thread_ids = [
        str(t["_id"])
        for t in db.ai_threads.find({"user_id": user_id}, {"_id": 1})
    ]
    if not thread_ids:
        return 0
    return db.ai_messages.count_documents(
        {
            "thread_id": {"$in": thread_ids},
            "role": "user",
            "created_at": {"$gte": start},
        }
    )


def basket_count(user_id: str) -> int:
    return db.baskets.count_documents({"user_id": user_id})


def entitlements_for(user_id: str) -> dict[str, Any]:
    plan = get_user_plan(user_id)
    keel = plan == "keel"
    used_ai = ai_messages_today(user_id)
    baskets = basket_count(user_id)
    ai_limit = None if keel else FREE_AI_PER_DAY
    basket_limit = None if keel else FREE_BASKET_LIMIT
    return {
        "plan": plan,
        "plan_name": PLANS[plan]["name"],
        "is_keel": keel,
        "limits": {
            "ai_per_day": ai_limit,
            "baskets": basket_limit,
            "discover_filters": "all" if keel else "basic",
        },
        "usage": {
            "ai_today": used_ai,
            "baskets": baskets,
        },
        "can": {
            "ai_chat": keel or used_ai < FREE_AI_PER_DAY,
            "create_basket": keel or baskets < FREE_BASKET_LIMIT,
            "all_filters": keel,
            "why_blurb": keel,
            "swipe_pulse": keel,
        },
        "free_filters": sorted(FREE_FILTERS),
    }


def assert_can_ai_chat(user_id: str) -> None:
    ent = entitlements_for(user_id)
    if not ent["can"]["ai_chat"]:
        raise PermissionError(
            f"Free plan includes {FREE_AI_PER_DAY} Ask AI messages per day. Upgrade to Keel for unlimited."
        )


def assert_can_create_basket(user_id: str) -> None:
    ent = entitlements_for(user_id)
    if not ent["can"]["create_basket"]:
        raise PermissionError(
            f"Free plan includes {FREE_BASKET_LIMIT} basket. Upgrade to Keel for unlimited baskets."
        )


def assert_can_use_filter(user_id: str | None, filter_key: str) -> None:
    key = (filter_key or "trending").lower()
    if key in FREE_FILTERS:
        return
    if user_id and is_keel(user_id):
        return
    # Guests / free users blocked from pro filters
    raise PermissionError(
        "That Discover filter is a Keel feature. Upgrade to unlock all filters."
    )


def upgrade_user(user_id: str, plan: str = "keel") -> dict[str, Any]:
    plan = normalize_plan(plan)
    if plan == "free":
        db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"plan": "free", "plan_updated_at": _now()}, "$unset": {"plan_expires_at": ""}},
        )
    else:
        # Mock: treat as active subscription (no payment provider yet)
        db.users.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "plan": plan,
                    "plan_updated_at": _now(),
                    "plan_source": "mock",
                }
            },
        )
    return entitlements_for(user_id)


def why_blurb(coin: dict[str, Any]) -> str:
    """Lightweight paid Discover blurb — no LLM required for MVP."""
    name = coin.get("name") or coin.get("symbol") or "This asset"
    ch = coin.get("price_change_percentage_24h")
    rank = coin.get("market_cap_rank")
    vol = coin.get("total_volume") or 0
    mcap = coin.get("market_cap") or 0

    bits: list[str] = []
    if isinstance(ch, (int, float)):
        if ch >= 5:
            bits.append(f"strong +{ch:.1f}% momentum in 24h")
        elif ch <= -5:
            bits.append(f"under pressure ({ch:.1f}% 24h) — watch for reclaim")
        else:
            bits.append(f"quiet tape ({ch:+.1f}% 24h)")
    if rank and rank <= 20:
        bits.append(f"blue-chip rank #{rank}")
    elif rank and rank <= 100:
        bits.append(f"mid-cap rank #{rank}")
    elif rank:
        bits.append(f"smaller-cap rank #{rank}")
    if mcap and vol and mcap > 0:
        turnover = vol / mcap
        if turnover > 0.15:
            bits.append("elevated relative volume")
        elif turnover < 0.02:
            bits.append("thin relative volume")

    if not bits:
        return f"{name}: worth a closer look — open the coin page for depth."
    return f"{name}: " + "; ".join(bits) + "."
