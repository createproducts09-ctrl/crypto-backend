from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    aware = _as_aware(dt)
    return aware.isoformat() if aware else None


def _coin_price(coin_id: str) -> float | None:
    coin = db.coins.find_one({"id": coin_id}, {"current_price": 1})
    if not coin:
        return None
    try:
        price = float(coin.get("current_price") or 0)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _serialize(doc: dict, coin: dict | None = None) -> dict[str, Any]:
    entry = float(doc.get("entry_price") or 0)
    current = None
    unrealized = None
    if coin and coin.get("current_price") is not None:
        try:
            current = float(coin["current_price"])
        except (TypeError, ValueError):
            current = None
        if current and entry > 0:
            unrealized = ((current - entry) / entry) * 100.0

    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id"),
        "coin_id": doc.get("coin_id"),
        "thesis": doc.get("thesis") or "",
        "side": doc.get("side") or "bull",
        "entry_price": entry,
        "entry_at": _iso(doc.get("entry_at")),
        "status": doc.get("status") or "open",
        "return_30d": doc.get("return_30d"),
        "return_90d": doc.get("return_90d"),
        "max_drawdown": doc.get("max_drawdown"),
        "source": doc.get("source") or "discover",
        "score_error": doc.get("score_error"),
        "close_reason": doc.get("close_reason"),
        "updated_at": _iso(doc.get("updated_at")),
        "current_price": current,
        "unrealized_pct": unrealized,
        "coin": coin,
    }


def _attach_coins(items: list[dict]) -> list[dict]:
    ids = [i["coin_id"] for i in items if i.get("coin_id")]
    coins = {c["id"]: c for c in db.coins.find({"id": {"$in": ids}}, {"_id": 0})}
    return [_serialize(i, coins.get(i["coin_id"])) for i in items]


def upsert_conviction(
    user_id: str,
    coin_id: str,
    thesis: str = "",
    side: str = "bull",
    source: str = "discover",
    entry_price: float | None = None,
) -> dict[str, Any]:
    coin_id = (coin_id or "").strip()
    if not coin_id:
        raise ValueError("coin_id required")
    thesis = (thesis or "").strip()[:140]
    side = (side or "bull").strip().lower()
    if side not in {"bull", "bear"}:
        side = "bull"
    source = (source or "discover").strip().lower()
    if source not in {"discover", "manual", "duel"}:
        source = "discover"

    price = entry_price if entry_price and entry_price > 0 else _coin_price(coin_id)
    if price is None or price <= 0:
        # Still allow creating with 0; scoring job will retry / mark error
        price = 0.0

    now = _now()
    existing = db.convictions.find_one(
        {"user_id": user_id, "coin_id": coin_id, "status": {"$in": ["open", "scored_30d"]}}
    )
    if existing:
        db.convictions.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "thesis": thesis if thesis else existing.get("thesis") or "",
                    "side": side,
                    "source": source,
                    "updated_at": now,
                    "score_error": None,
                }
            },
        )
        doc = db.convictions.find_one({"_id": existing["_id"]})
        coin = db.coins.find_one({"id": coin_id}, {"_id": 0})
        return _serialize(doc, coin)

    doc = {
        "user_id": user_id,
        "coin_id": coin_id,
        "thesis": thesis,
        "side": side,
        "entry_price": float(price),
        "entry_at": now,
        "status": "open",
        "return_30d": None,
        "return_90d": None,
        "max_drawdown": None,
        "source": source,
        "score_error": None,
        "updated_at": now,
    }
    res = db.convictions.insert_one(doc)
    doc["_id"] = res.inserted_id
    coin = db.coins.find_one({"id": coin_id}, {"_id": 0})
    return _serialize(doc, coin)


def list_convictions(user_id: str, status: str | None = None) -> list[dict]:
    query: dict[str, Any] = {"user_id": user_id}
    if status:
        query["status"] = status
    items = list(db.convictions.find(query).sort("entry_at", -1).limit(200))
    return _attach_coins(items)


def get_summary(user_id: str) -> dict[str, Any]:
    items = list(db.convictions.find({"user_id": user_id}).sort("entry_at", -1).limit(500))
    scored = [i for i in items if i.get("return_30d") is not None or i.get("return_90d") is not None]
    returns_30 = [float(i["return_30d"]) for i in scored if i.get("return_30d") is not None]
    returns_90 = [float(i["return_90d"]) for i in scored if i.get("return_90d") is not None]

    def hit_rate_pct() -> float | None:
        hits = 0
        n = 0
        for doc in scored:
            side = doc.get("side") or "bull"
            ret = doc.get("return_30d")
            if ret is None:
                ret = doc.get("return_90d")
            if ret is None:
                continue
            n += 1
            if side == "bull" and float(ret) > 0:
                hits += 1
            elif side == "bear" and float(ret) < 0:
                hits += 1
        return (hits / n) * 100.0 if n else None

    best = None
    worst = None
    for doc in scored:
        ret = doc.get("return_90d")
        if ret is None:
            ret = doc.get("return_30d")
        if ret is None:
            continue
        packed = {
            "coin_id": doc.get("coin_id"),
            "thesis": doc.get("thesis") or "",
            "return_pct": float(ret),
            "side": doc.get("side") or "bull",
        }
        if best is None or packed["return_pct"] > best["return_pct"]:
            best = packed
        if worst is None or packed["return_pct"] < worst["return_pct"]:
            worst = packed

    week_ago = _now() - timedelta(days=7)
    weekly = []
    for i in items:
        entry_at = _as_aware(i.get("entry_at"))
        if entry_at and entry_at >= week_ago:
            weekly.append(i)
    weekly_unrealized = []
    coin_ids = [i["coin_id"] for i in weekly]
    coins = {c["id"]: c for c in db.coins.find({"id": {"$in": coin_ids}}, {"_id": 0, "id": 1, "current_price": 1})}
    for i in weekly:
        entry = float(i.get("entry_price") or 0)
        cur = coins.get(i["coin_id"], {}).get("current_price")
        if entry > 0 and cur:
            try:
                weekly_unrealized.append(((float(cur) - entry) / entry) * 100.0)
            except (TypeError, ValueError):
                pass

    return {
        "open_count": sum(1 for i in items if i.get("status") in {"open", "scored_30d"}),
        "scored_count": len(scored),
        "avg_return_30d": sum(returns_30) / len(returns_30) if returns_30 else None,
        "avg_return_90d": sum(returns_90) / len(returns_90) if returns_90 else None,
        "hit_rate_pct": hit_rate_pct(),
        "best": best,
        "worst": worst,
        "weekly": {
            "new_convictions": len(weekly),
            "avg_unrealized_pct": sum(weekly_unrealized) / len(weekly_unrealized) if weekly_unrealized else None,
        },
    }


def patch_conviction(user_id: str, conviction_id: str, patch: dict[str, Any]) -> dict | None:
    try:
        oid = ObjectId(conviction_id)
    except (InvalidId, TypeError):
        return None
    doc = db.convictions.find_one({"_id": oid, "user_id": user_id})
    if not doc:
        return None

    updates: dict[str, Any] = {"updated_at": _now()}
    if "thesis" in patch:
        updates["thesis"] = str(patch.get("thesis") or "").strip()[:140]
    if "side" in patch:
        side = str(patch.get("side") or "bull").lower()
        if side in {"bull", "bear"}:
            updates["side"] = side
    if patch.get("close"):
        updates["status"] = "closed"
        updates["close_reason"] = patch.get("close_reason") or "user"

    db.convictions.update_one({"_id": oid}, {"$set": updates})
    doc = db.convictions.find_one({"_id": oid})
    coin = db.coins.find_one({"id": doc["coin_id"]}, {"_id": 0})
    return _serialize(doc, coin)


def delete_conviction(user_id: str, conviction_id: str) -> bool:
    try:
        oid = ObjectId(conviction_id)
    except (InvalidId, TypeError):
        return False
    res = db.convictions.delete_one({"_id": oid, "user_id": user_id})
    return res.deleted_count > 0


def close_open_for_coin(user_id: str, coin_id: str, reason: str = "passed") -> int:
    res = db.convictions.update_many(
        {"user_id": user_id, "coin_id": coin_id, "status": {"$in": ["open", "scored_30d"]}},
        {"$set": {"status": "closed", "close_reason": reason, "updated_at": _now()}},
    )
    return res.modified_count


def _return_from_prices(entry: float, exit_price: float) -> float:
    return ((exit_price - entry) / entry) * 100.0


def score_convictions(limit: int = 80) -> dict[str, Any]:
    """Progressively score convictions at 30d and 90d."""
    now = _now()
    scored_30 = 0
    scored_90 = 0
    errors = 0
    closed = 0

    candidates = list(
        db.convictions.find({"status": {"$in": ["open", "scored_30d"]}})
        .sort("entry_at", 1)
        .limit(limit)
    )

    for doc in candidates:
        entry_at = doc.get("entry_at")
        if not entry_at:
            continue
        if entry_at.tzinfo is None:
            entry_at = entry_at.replace(tzinfo=timezone.utc)

        entry_price = float(doc.get("entry_price") or 0)
        coin_id = doc.get("coin_id")
        coin = db.coins.find_one({"id": coin_id}, {"_id": 0}) if coin_id else None

        if not coin:
            db.convictions.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "closed",
                        "close_reason": "delisted",
                        "updated_at": now,
                        "score_error": "coin_missing",
                    }
                },
            )
            closed += 1
            continue

        if entry_price <= 0:
            price = _coin_price(coin_id)
            if price and price > 0:
                entry_price = price
                db.convictions.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"entry_price": entry_price, "updated_at": now}},
                )
            else:
                db.convictions.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"score_error": "zero_entry_price", "updated_at": now}},
                )
                errors += 1
                continue

        age = now - entry_at
        current = float(coin.get("current_price") or 0)
        if current <= 0:
            db.convictions.update_one(
                {"_id": doc["_id"]},
                {"$set": {"score_error": "missing_price", "updated_at": now}},
            )
            errors += 1
            continue

        updates: dict[str, Any] = {"score_error": None, "updated_at": now}

        # Approximate drawdown vs entry using current if underwater
        dd = min(0.0, _return_from_prices(entry_price, current))
        prev_dd = doc.get("max_drawdown")
        if prev_dd is None or dd < float(prev_dd):
            updates["max_drawdown"] = dd

        if doc.get("status") == "open" and age >= timedelta(days=30):
            # Prefer cached 30d % when entry was ~now-30d; else use price delta from entry
            pct_30 = coin.get("price_change_percentage_30d")
            if pct_30 is not None and abs(age.days - 30) <= 3:
                updates["return_30d"] = float(pct_30)
            else:
                updates["return_30d"] = _return_from_prices(entry_price, current)
            updates["status"] = "scored_30d"
            scored_30 += 1

        if doc.get("status") in {"open", "scored_30d"} and age >= timedelta(days=90):
            updates["return_90d"] = _return_from_prices(entry_price, current)
            updates["status"] = "scored_90d"
            scored_90 += 1

        if len(updates) > 2:
            db.convictions.update_one({"_id": doc["_id"]}, {"$set": updates})

    return {"scored_30": scored_30, "scored_90": scored_90, "errors": errors, "closed": closed}
