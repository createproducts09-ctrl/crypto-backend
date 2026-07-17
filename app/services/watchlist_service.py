from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.extensions import db


def _oid(user_id: str) -> ObjectId:
    return ObjectId(user_id)


def get_watchlist(user_id: str) -> list[dict]:
    items = list(db.watchlist.find({"user_id": user_id}).sort("created_at", -1))
    coin_ids = [i["coin_id"] for i in items]
    coins = {c["id"]: c for c in db.coins.find({"id": {"$in": coin_ids}}, {"_id": 0})}
    out = []
    for item in items:
        coin = coins.get(item["coin_id"], {"id": item["coin_id"]})
        out.append(
            {
                "id": str(item["_id"]),
                "coin_id": item["coin_id"],
                "category": item.get("category") or "default",
                "notes": item.get("notes") or "",
                "tags": item.get("tags") or [],
                "created_at": item.get("created_at").isoformat() if item.get("created_at") else None,
                "coin": coin,
            }
        )
    return out


def add_to_watchlist(user_id: str, coin_id: str, category: str = "default", notes: str = "", tags: list | None = None) -> dict:
    existing = db.watchlist.find_one({"user_id": user_id, "coin_id": coin_id})
    if existing:
        return {"id": str(existing["_id"]), "coin_id": coin_id, "already_exists": True}
    doc = {
        "user_id": user_id,
        "coin_id": coin_id,
        "category": category,
        "notes": notes,
        "tags": tags or [],
        "created_at": datetime.now(timezone.utc),
    }
    res = db.watchlist.insert_one(doc)
    return {"id": str(res.inserted_id), "coin_id": coin_id, "already_exists": False}


def update_watchlist_item(user_id: str, item_id: str, patch: dict[str, Any]) -> dict | None:
    allowed = {"category", "notes", "tags"}
    data = {k: v for k, v in patch.items() if k in allowed}
    db.watchlist.update_one({"_id": ObjectId(item_id), "user_id": user_id}, {"$set": data})
    item = db.watchlist.find_one({"_id": ObjectId(item_id), "user_id": user_id})
    if not item:
        return None
    return {
        "id": str(item["_id"]),
        "coin_id": item["coin_id"],
        "category": item.get("category"),
        "notes": item.get("notes"),
        "tags": item.get("tags") or [],
    }


def remove_from_watchlist(user_id: str, coin_id: str) -> bool:
    res = db.watchlist.delete_one({"user_id": user_id, "coin_id": coin_id})
    return res.deleted_count > 0
