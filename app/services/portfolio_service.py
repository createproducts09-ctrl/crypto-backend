from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> ObjectId | None:
    try:
        if not value or not ObjectId.is_valid(value):
            return None
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _coins_map(coin_ids: list[str]) -> dict[str, dict]:
    if not coin_ids:
        return {}
    return {c["id"]: c for c in db.coins.find({"id": {"$in": coin_ids}}, {"_id": 0})}


def _enrich_asset(asset: dict, coin: dict | None = None) -> dict:
    coin = coin or {}
    price = float(coin.get("current_price") or 0)
    amount = float(asset.get("amount") or 0)
    avg_price = asset.get("avg_price")
    if avg_price is None and amount and asset.get("cost_basis"):
        avg_price = float(asset["cost_basis"]) / amount
    else:
        avg_price = float(avg_price or 0)
    cost_basis = float(asset.get("cost_basis") or (amount * avg_price))
    value = price * amount
    pnl = value - cost_basis if amount else 0.0
    return {
        "coin_id": asset["coin_id"],
        "amount": amount,
        "avg_price": avg_price,
        "cost_basis": cost_basis,
        "value": value,
        "pnl": pnl,
        "pnl_pct": (pnl / cost_basis * 100) if cost_basis else None,
        "is_holding": amount > 0,
        "coin": {
            "id": coin.get("id") or asset["coin_id"],
            "name": coin.get("name") or asset["coin_id"],
            "symbol": coin.get("symbol") or asset["coin_id"],
            "image": coin.get("image"),
            "current_price": price,
            "price_change_percentage_24h": coin.get("price_change_percentage_24h"),
            "market_cap_rank": coin.get("market_cap_rank"),
        },
    }


def _serialize_basket(doc: dict, enrich: bool = True) -> dict:
    assets = doc.get("assets") or []
    out_assets = []
    total_value = 0.0
    total_cost = 0.0
    if enrich:
        coins = _coins_map([a["coin_id"] for a in assets if a.get("coin_id")])
        for a in assets:
            if not a.get("coin_id"):
                continue
            item = _enrich_asset(a, coins.get(a["coin_id"]))
            out_assets.append(item)
            if item["is_holding"]:
                total_value += item["value"]
                total_cost += item["cost_basis"]
    else:
        out_assets = assets

    pnl = total_value - total_cost
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "Basket",
        "note": doc.get("note") or "",
        "asset_count": len(out_assets),
        "holding_count": sum(1 for a in out_assets if a.get("is_holding")),
        "total_value": total_value,
        "total_cost": total_cost,
        "pnl": pnl,
        "pnl_pct": (pnl / total_cost * 100) if total_cost else None,
        "assets": out_assets,
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


def portfolio_summary(user_id: str) -> dict[str, Any]:
    baskets = list(db.baskets.find({"user_id": user_id}).sort("updated_at", -1))
    serialized = [_serialize_basket(b) for b in baskets]
    total_value = sum(b["total_value"] for b in serialized)
    total_cost = sum(b["total_cost"] for b in serialized)
    pnl = total_value - total_cost
    return {
        "total_value": total_value,
        "total_cost": total_cost,
        "pnl": pnl,
        "pnl_pct": (pnl / total_cost * 100) if total_cost else None,
        "basket_count": len(serialized),
        "baskets": [
            {
                "id": b["id"],
                "name": b["name"],
                "note": b["note"],
                "asset_count": b["asset_count"],
                "holding_count": b["holding_count"],
                "total_value": b["total_value"],
                "total_cost": b["total_cost"],
                "pnl": b["pnl"],
                "pnl_pct": b["pnl_pct"],
                "updated_at": b["updated_at"],
            }
            for b in serialized
        ],
        "holdings": [
            {**a, "id": f"{b['id']}:{a['coin_id']}", "basket_id": b["id"], "basket_name": b["name"]}
            for b in serialized
            for a in b["assets"]
            if a.get("is_holding")
        ],
    }


def list_baskets(user_id: str) -> list[dict]:
    baskets = list(db.baskets.find({"user_id": user_id}).sort("updated_at", -1))
    return [_serialize_basket(b) for b in baskets]


def get_basket(user_id: str, basket_id: str) -> dict | None:
    oid = _oid(basket_id)
    if not oid:
        return None
    doc = db.baskets.find_one({"_id": oid, "user_id": user_id})
    if not doc:
        return None
    return _serialize_basket(doc)


def create_basket(
    user_id: str,
    name: str,
    coin_ids: list[str] | None = None,
    import_watchlist: bool = False,
    note: str = "",
) -> dict:
    clean_name = (name or "").strip()[:60] or "My basket"
    ids: list[str] = []
    seen: set[str] = set()

    # Explicit selection is source of truth when provided
    for cid in coin_ids or []:
        cid = str(cid or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)

    # Only import watchlist when no explicit selection was sent
    if import_watchlist and not (coin_ids or []):
        for item in db.watchlist.find({"user_id": user_id}):
            cid = (item.get("coin_id") or "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)

    now = _now()
    doc = {
        "user_id": user_id,
        "name": clean_name,
        "note": (note or "").strip()[:200],
        "assets": [{"coin_id": cid, "amount": 0.0, "avg_price": 0.0, "cost_basis": 0.0} for cid in ids],
        "created_at": now,
        "updated_at": now,
    }
    res = db.baskets.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize_basket(doc)


def update_basket(user_id: str, basket_id: str, patch: dict[str, Any]) -> dict | None:
    oid = _oid(basket_id)
    if not oid:
        return None
    data: dict[str, Any] = {"updated_at": _now()}
    if "name" in patch:
        cleaned = str(patch.get("name") or "").strip()[:60]
        if not cleaned:
            return None
        data["name"] = cleaned
    if "note" in patch:
        data["note"] = str(patch.get("note") or "").strip()[:200]
    res = db.baskets.update_one({"_id": oid, "user_id": user_id}, {"$set": data})
    if not res.matched_count:
        return None
    return get_basket(user_id, basket_id)


def delete_basket(user_id: str, basket_id: str) -> bool:
    oid = _oid(basket_id)
    if not oid:
        return False
    res = db.baskets.delete_one({"_id": oid, "user_id": user_id})
    return res.deleted_count > 0


def add_asset(user_id: str, basket_id: str, coin_id: str) -> dict | None:
    oid = _oid(basket_id)
    coin_id = (coin_id or "").strip()
    if not oid or not coin_id:
        return None
    # Atomic: only push if coin not already present
    res = db.baskets.update_one(
        {
            "_id": oid,
            "user_id": user_id,
            "assets.coin_id": {"$ne": coin_id},
        },
        {
            "$push": {
                "assets": {"coin_id": coin_id, "amount": 0.0, "avg_price": 0.0, "cost_basis": 0.0}
            },
            "$set": {"updated_at": _now()},
        },
    )
    if res.matched_count == 0:
        exists = db.baskets.find_one({"_id": oid, "user_id": user_id}, {"_id": 1})
        if not exists:
            return None
    return get_basket(user_id, basket_id)


def remove_asset(user_id: str, basket_id: str, coin_id: str) -> dict | None:
    oid = _oid(basket_id)
    coin_id = (coin_id or "").strip()
    if not oid or not coin_id:
        return None
    res = db.baskets.update_one(
        {"_id": oid, "user_id": user_id},
        {"$pull": {"assets": {"coin_id": coin_id}}, "$set": {"updated_at": _now()}},
    )
    if not res.matched_count:
        return None
    return get_basket(user_id, basket_id)


def set_holding(
    user_id: str,
    basket_id: str,
    coin_id: str,
    amount: float,
    avg_price: float | None = None,
    cost_basis: float | None = None,
) -> dict | None:
    oid = _oid(basket_id)
    coin_id = (coin_id or "").strip()
    if not oid or not coin_id:
        return None

    amt = float(amount or 0)
    if amt < 0:
        raise ValueError("amount must be >= 0")
    if avg_price is not None and float(avg_price) < 0:
        raise ValueError("avg_price must be >= 0")
    if cost_basis is not None and float(cost_basis) < 0:
        raise ValueError("cost_basis must be >= 0")

    if avg_price is not None:
        avg = float(avg_price or 0)
        cost = amt * avg
    elif cost_basis is not None:
        cost = float(cost_basis or 0)
        avg = (cost / amt) if amt else 0.0
    else:
        avg = 0.0
        cost = 0.0

    # Try update existing asset
    res = db.baskets.update_one(
        {"_id": oid, "user_id": user_id, "assets.coin_id": coin_id},
        {
            "$set": {
                "assets.$.amount": amt,
                "assets.$.avg_price": avg,
                "assets.$.cost_basis": cost,
                "updated_at": _now(),
            }
        },
    )
    if res.matched_count:
        return get_basket(user_id, basket_id)

    # Ensure basket exists, then push
    exists = db.baskets.find_one({"_id": oid, "user_id": user_id}, {"_id": 1})
    if not exists:
        return None
    db.baskets.update_one(
        {"_id": oid, "user_id": user_id},
        {
            "$push": {
                "assets": {
                    "coin_id": coin_id,
                    "amount": amt,
                    "avg_price": avg,
                    "cost_basis": cost,
                }
            },
            "$set": {"updated_at": _now()},
        },
    )
    return get_basket(user_id, basket_id)


def list_holdings(user_id: str) -> dict[str, Any]:
    return portfolio_summary(user_id)


def upsert_holding(user_id: str, coin_id: str, amount: float, cost_basis: float) -> dict:
    coin_id = (coin_id or "").strip()
    if not coin_id:
        raise ValueError("coin_id required")
    amount = float(amount or 0)
    cost_basis = float(cost_basis or 0)
    if amount < 0 or cost_basis < 0:
        raise ValueError("amount and cost_basis must be >= 0")

    basket = db.baskets.find_one({"user_id": user_id, "name": "Holdings"})
    if not basket:
        created = create_basket(user_id, "Holdings", coin_ids=[])
        # Prevent duplicate race: unique-ish by re-fetch
        basket = db.baskets.find_one({"user_id": user_id, "name": "Holdings"}) or {"_id": ObjectId(created["id"])}
        # If race created extras named Holdings, keep using first
        extras = list(db.baskets.find({"user_id": user_id, "name": "Holdings"}).sort("created_at", 1))
        if len(extras) > 1:
            keep = extras[0]["_id"]
            for extra in extras[1:]:
                db.baskets.delete_one({"_id": extra["_id"]})
            basket = db.baskets.find_one({"_id": keep})

    basket_id = str(basket["_id"])
    avg = (cost_basis / amount) if amount else 0.0
    set_holding(user_id, basket_id, coin_id, amount, avg_price=avg, cost_basis=cost_basis)
    return {"id": f"{basket_id}:{coin_id}", "coin_id": coin_id, "amount": amount, "cost_basis": cost_basis}


def delete_holding(user_id: str, holding_id: str) -> bool:
    if ":" in holding_id:
        basket_id, coin_id = holding_id.split(":", 1)
        result = set_holding(user_id, basket_id, coin_id, 0, avg_price=0)
        return result is not None
    oid = _oid(holding_id)
    if not oid:
        return False
    res = db.portfolio.delete_one({"_id": oid, "user_id": user_id})
    return res.deleted_count > 0
