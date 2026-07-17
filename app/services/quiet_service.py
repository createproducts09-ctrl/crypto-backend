from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


DEFAULT_PREFS = {
    "enabled": False,
    "cool_hours": 12,
    "soft_block_enabled": True,
}


def get_prefs(user_id: str) -> dict[str, Any]:
    doc = db.quiet_prefs.find_one({"user_id": user_id}) or {}
    return {
        "enabled": bool(doc.get("enabled", DEFAULT_PREFS["enabled"])),
        "cool_hours": int(doc.get("cool_hours", DEFAULT_PREFS["cool_hours"])),
        "soft_block_enabled": bool(doc.get("soft_block_enabled", DEFAULT_PREFS["soft_block_enabled"])),
    }


def update_prefs(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_prefs(user_id)
    if "enabled" in patch:
        current["enabled"] = bool(patch["enabled"])
    if "soft_block_enabled" in patch:
        current["soft_block_enabled"] = bool(patch["soft_block_enabled"])
    if "cool_hours" in patch:
        try:
            hours = int(patch["cool_hours"])
        except (TypeError, ValueError):
            raise ValueError("cool_hours must be an integer") from None
        current["cool_hours"] = max(6, min(24, hours))

    db.quiet_prefs.update_one(
        {"user_id": user_id},
        {"$set": {**current, "user_id": user_id, "updated_at": _now()}},
        upsert=True,
    )
    return current


def _serialize_bag(doc: dict, coin: dict | None = None) -> dict[str, Any]:
    ready_at = doc.get("ready_at")
    now = _now()
    if ready_at and ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    ready = bool(ready_at and ready_at <= now)
    secs = 0
    if ready_at and not ready:
        secs = max(0, int((ready_at - now).total_seconds()))
    return {
        "id": str(doc["_id"]),
        "coin_id": doc.get("coin_id"),
        "ready_at": _iso(ready_at),
        "created_at": _iso(doc.get("created_at")),
        "ready": ready,
        "seconds_remaining": secs,
        "coin": coin,
    }


def list_cooling_bag(user_id: str) -> list[dict]:
    items = list(db.cooling_bag.find({"user_id": user_id}).sort("ready_at", 1))
    ids = [i["coin_id"] for i in items]
    coins = {c["id"]: c for c in db.coins.find({"id": {"$in": ids}}, {"_id": 0})}
    return [_serialize_bag(i, coins.get(i["coin_id"])) for i in items]


def add_to_cooling_bag(user_id: str, coin_id: str, cool_hours: int | None = None) -> dict[str, Any]:
    coin_id = (coin_id or "").strip()
    if not coin_id:
        raise ValueError("coin_id required")
    prefs = get_prefs(user_id)
    hours = cool_hours if cool_hours is not None else prefs["cool_hours"]
    hours = max(6, min(24, int(hours)))
    now = _now()
    ready_at = now + timedelta(hours=hours)

    existing = db.cooling_bag.find_one({"user_id": user_id, "coin_id": coin_id})
    if existing:
        # Keep the earlier ready_at if already cooling (don't extend unfairly)
        prev = existing.get("ready_at")
        if prev and prev.tzinfo is None:
            prev = prev.replace(tzinfo=timezone.utc)
        if prev and prev <= ready_at:
            ready_at = prev
        db.cooling_bag.update_one(
            {"_id": existing["_id"]},
            {"$set": {"ready_at": ready_at, "updated_at": now}},
        )
        doc = db.cooling_bag.find_one({"_id": existing["_id"]})
    else:
        doc = {
            "user_id": user_id,
            "coin_id": coin_id,
            "ready_at": ready_at,
            "created_at": now,
            "updated_at": now,
        }
        res = db.cooling_bag.insert_one(doc)
        doc["_id"] = res.inserted_id

    coin = db.coins.find_one({"id": coin_id}, {"_id": 0})
    return _serialize_bag(doc, coin)


def get_bag_item(user_id: str, coin_id: str) -> dict | None:
    doc = db.cooling_bag.find_one({"user_id": user_id, "coin_id": coin_id})
    if not doc:
        return None
    coin = db.coins.find_one({"id": coin_id}, {"_id": 0})
    return _serialize_bag(doc, coin)


def release_from_bag(
    user_id: str,
    coin_id: str,
    force: bool = False,
) -> dict[str, Any]:
    coin_id = (coin_id or "").strip()
    doc = db.cooling_bag.find_one({"user_id": user_id, "coin_id": coin_id})
    if not doc:
        raise ValueError("not_in_bag")
    ready_at = doc.get("ready_at")
    if ready_at and ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    ready = bool(ready_at and ready_at <= _now())
    if not ready and not force:
        raise ValueError("not_ready")
    db.cooling_bag.delete_one({"_id": doc["_id"]})
    return {"ok": True, "coin_id": coin_id, "forced": bool(force and not ready)}


def is_cooling(user_id: str, coin_id: str) -> bool:
    """True if coin is in bag and not yet ready."""
    item = get_bag_item(user_id, coin_id)
    if not item:
        return False
    return not item["ready"]


def cleanup_ready_expired(max_age_hours: int = 72) -> dict[str, int]:
    """Remove bag items that have been ready for a long time (user never released)."""
    cutoff = _now() - timedelta(hours=max_age_hours)
    # Items ready long ago
    res = db.cooling_bag.delete_many({"ready_at": {"$lt": cutoff}})
    return {"deleted": res.deleted_count}
