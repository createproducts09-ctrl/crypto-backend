from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import db

BLOCKED = re.compile(r"\b(scam|rug|guaranteed|pump\s*and\s*dump)\b", re.I)
HORIZONS = {"7d": 7, "30d": 30}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _anon_tag(user_id: str, salt: str = "") -> str:
    digest = hashlib.sha256(f"{user_id}:{salt}".encode()).hexdigest()[:4].upper()
    return f"Signal-{digest}"


def _coin_price(coin_id: str) -> float | None:
    coin = db.coins.find_one({"id": coin_id}, {"current_price": 1})
    if not coin:
        return None
    try:
        price = float(coin.get("current_price") or 0)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _serialize_entry(doc: dict, coin: dict | None = None, hide_user: bool = True) -> dict[str, Any]:
    out = {
        "id": str(doc["_id"]),
        "coin_id": doc.get("coin_id"),
        "side": doc.get("side"),
        "thesis": doc.get("thesis"),
        "horizon": doc.get("horizon"),
        "entry_price": doc.get("entry_price"),
        "status": doc.get("status"),
        "anonymous_tag": doc.get("anonymous_tag"),
        "duel_id": str(doc["duel_id"]) if doc.get("duel_id") else None,
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "coin": coin,
    }
    if not hide_user:
        out["user_id"] = doc.get("user_id")
    return out


def _serialize_duel(doc: dict, entries: dict[str, dict] | None = None, coin: dict | None = None) -> dict[str, Any]:
    entries = entries or {}
    bull = entries.get(str(doc.get("bull_entry_id")))
    bear = entries.get(str(doc.get("bear_entry_id")))
    return {
        "id": str(doc["_id"]),
        "coin_id": doc.get("coin_id"),
        "horizon": doc.get("horizon"),
        "entry_price": doc.get("entry_price"),
        "exit_price": doc.get("exit_price"),
        "move_pct": doc.get("move_pct"),
        "resolve_at": _iso(doc.get("resolve_at")),
        "status": doc.get("status"),
        "winner": doc.get("winner"),
        "cancel_reason": doc.get("cancel_reason"),
        "retry_count": doc.get("retry_count") or 0,
        "created_at": _iso(doc.get("created_at")),
        "coin": coin,
        "bull": bull,
        "bear": bear,
    }


def create_entry(
    user_id: str,
    coin_id: str,
    side: str,
    thesis: str,
    horizon: str = "7d",
) -> dict[str, Any]:
    coin_id = (coin_id or "").strip()
    side = (side or "").strip().lower()
    thesis = (thesis or "").strip()
    horizon = (horizon or "7d").strip().lower()

    if not coin_id:
        raise ValueError("coin_id required")
    if side not in {"bull", "bear"}:
        raise ValueError("side must be bull or bear")
    if horizon not in HORIZONS:
        raise ValueError("horizon must be 7d or 30d")
    if len(thesis) < 20 or len(thesis) > 280:
        raise ValueError("thesis must be 20–280 characters")
    if BLOCKED.search(thesis):
        raise ValueError("thesis contains blocked language")

    # Rate limit: 5 / hour
    hour_ago = _now() - timedelta(hours=1)
    recent = db.duel_entries.count_documents(
        {"user_id": user_id, "created_at": {"$gte": hour_ago}}
    )
    if recent >= 5:
        raise ValueError("rate_limit: max 5 duel entries per hour")

    existing_waiting = db.duel_entries.find_one(
        {
            "user_id": user_id,
            "coin_id": coin_id,
            "horizon": horizon,
            "status": "waiting",
        }
    )
    if existing_waiting:
        raise ValueError("already_waiting: cancel existing entry first")

    price = _coin_price(coin_id)
    if price is None:
        raise ValueError("coin_price_unavailable")

    now = _now()
    doc = {
        "user_id": user_id,
        "coin_id": coin_id,
        "side": side,
        "thesis": thesis,
        "horizon": horizon,
        "entry_price": price,
        "status": "waiting",
        "anonymous_tag": _anon_tag(user_id, f"{coin_id}:{horizon}:{now.isoformat()}"),
        "duel_id": None,
        "created_at": now,
        "updated_at": now,
    }
    res = db.duel_entries.insert_one(doc)
    doc["_id"] = res.inserted_id

    opposite = "bear" if side == "bull" else "bull"
    match = db.duel_entries.find_one(
        {
            "coin_id": coin_id,
            "horizon": horizon,
            "side": opposite,
            "status": "waiting",
            "user_id": {"$ne": user_id},
        },
        sort=[("created_at", 1)],
    )

    duel = None
    if match:
        days = HORIZONS[horizon]
        avg_price = (float(match["entry_price"]) + price) / 2.0
        duel_doc = {
            "coin_id": coin_id,
            "horizon": horizon,
            "bull_entry_id": doc["_id"] if side == "bull" else match["_id"],
            "bear_entry_id": doc["_id"] if side == "bear" else match["_id"],
            "entry_price": avg_price,
            "exit_price": None,
            "move_pct": None,
            "resolve_at": now + timedelta(days=days),
            "status": "matched",
            "winner": None,
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        dres = db.duels.insert_one(duel_doc)
        duel_id = dres.inserted_id
        db.duel_entries.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": "matched", "duel_id": duel_id, "updated_at": now}},
        )
        db.duel_entries.update_one(
            {"_id": match["_id"]},
            {"$set": {"status": "matched", "duel_id": duel_id, "updated_at": now}},
        )
        doc["status"] = "matched"
        doc["duel_id"] = duel_id
        duel_doc["_id"] = duel_id
        duel = duel_doc

    coin = db.coins.find_one({"id": coin_id}, {"_id": 0})
    result = {
        "entry": _serialize_entry(doc, coin, hide_user=False),
        "matched": bool(duel),
        "duel": None,
    }
    if duel:
        entries = {
            str(doc["_id"]): _serialize_entry(doc, coin),
            str(match["_id"]): _serialize_entry(match, coin),
        }
        result["duel"] = _serialize_duel(duel, entries, coin)
    return result


def cancel_entry(user_id: str, entry_id: str) -> dict[str, Any]:
    try:
        oid = ObjectId(entry_id)
    except (InvalidId, TypeError):
        raise ValueError("invalid_id") from None
    doc = db.duel_entries.find_one({"_id": oid, "user_id": user_id})
    if not doc:
        raise ValueError("not_found")
    if doc.get("status") != "waiting":
        raise ValueError("cannot_cancel: only waiting entries can be cancelled")
    db.duel_entries.update_one(
        {"_id": oid},
        {"$set": {"status": "cancelled", "updated_at": _now()}},
    )
    return {"ok": True, "id": entry_id}


def list_mine(user_id: str) -> dict[str, Any]:
    entries = list(db.duel_entries.find({"user_id": user_id}).sort("created_at", -1).limit(100))
    coin_ids = list({e["coin_id"] for e in entries})
    coins = {c["id"]: c for c in db.coins.find({"id": {"$in": coin_ids}}, {"_id": 0})}

    duel_ids = [e["duel_id"] for e in entries if e.get("duel_id")]
    duels = {d["_id"]: d for d in db.duels.find({"_id": {"$in": duel_ids}})} if duel_ids else {}

    waiting = []
    live = []
    resolved = []
    for e in entries:
        ser = _serialize_entry(e, coins.get(e["coin_id"]), hide_user=False)
        st = e.get("status")
        if st == "waiting":
            waiting.append(ser)
        elif st == "matched":
            duel = duels.get(e.get("duel_id"))
            if duel:
                ser["duel"] = _pack_duel(duel, coins.get(e["coin_id"]))
            live.append(ser)
        elif st == "resolved":
            duel = duels.get(e.get("duel_id"))
            if duel:
                ser["duel"] = _pack_duel(duel, coins.get(e["coin_id"]))
            resolved.append(ser)
        elif st == "cancelled":
            resolved.append(ser)
    return {"waiting": waiting, "live": live, "resolved": resolved}


def _pack_duel(duel: dict, coin: dict | None) -> dict:
    entry_ids = [duel.get("bull_entry_id"), duel.get("bear_entry_id")]
    entries_raw = list(db.duel_entries.find({"_id": {"$in": entry_ids}}))
    entries = {str(e["_id"]): _serialize_entry(e, coin) for e in entries_raw}
    return _serialize_duel(duel, entries, coin)


def get_duel(duel_id: str, user_id: str | None = None) -> dict | None:
    try:
        oid = ObjectId(duel_id)
    except (InvalidId, TypeError):
        return None
    duel = db.duels.find_one({"_id": oid})
    if not duel:
        return None
    coin = db.coins.find_one({"id": duel["coin_id"]}, {"_id": 0})
    packed = _pack_duel(duel, coin)
    if user_id:
        mine = db.duel_entries.find_one({"duel_id": oid, "user_id": user_id})
        packed["my_side"] = mine.get("side") if mine else None
        packed["my_entry_id"] = str(mine["_id"]) if mine else None
    return packed


def feed(limit: int = 40) -> list[dict]:
    items = list(
        db.duels.find({"status": {"$in": ["matched", "resolved"]}})
        .sort("created_at", -1)
        .limit(limit)
    )
    coin_ids = list({d["coin_id"] for d in items})
    coins = {c["id"]: c for c in db.coins.find({"id": {"$in": coin_ids}}, {"_id": 0})}
    return [_pack_duel(d, coins.get(d["coin_id"])) for d in items]


def leaderboard(limit: int = 20) -> list[dict]:
    pipeline = [
        {"$match": {"status": "resolved", "winner": {"$in": ["bull", "bear"]}}},
        {
            "$lookup": {
                "from": "duel_entries",
                "localField": "bull_entry_id",
                "foreignField": "_id",
                "as": "bull_e",
            }
        },
        {
            "$lookup": {
                "from": "duel_entries",
                "localField": "bear_entry_id",
                "foreignField": "_id",
                "as": "bear_e",
            }
        },
        {"$limit": 500},
    ]
    # Simpler approach without complex aggregation for pymongo compatibility
    wins: dict[str, dict] = {}
    for duel in db.duels.find({"status": "resolved", "winner": {"$in": ["bull", "bear"]}}).limit(500):
        winner = duel["winner"]
        entry_id = duel["bull_entry_id"] if winner == "bull" else duel["bear_entry_id"]
        entry = db.duel_entries.find_one({"_id": entry_id})
        if not entry:
            continue
        uid = entry["user_id"]
        tag = entry.get("anonymous_tag") or _anon_tag(uid)
        if uid not in wins:
            wins[uid] = {"anonymous_tag": tag, "wins": 0, "user_id": uid}
        wins[uid]["wins"] += 1

    ranked = sorted(wins.values(), key=lambda x: x["wins"], reverse=True)[:limit]
    for r in ranked:
        r.pop("user_id", None)
    return ranked


def resolve_duels(limit: int = 50) -> dict[str, Any]:
    now = _now()
    resolved = 0
    delayed = 0
    cancelled = 0

    due = list(
        db.duels.find({"status": "matched", "resolve_at": {"$lte": now}})
        .sort("resolve_at", 1)
        .limit(limit)
    )

    for duel in due:
        coin_id = duel.get("coin_id")
        coin = db.coins.find_one({"id": coin_id}, {"_id": 0}) if coin_id else None
        entry_price = float(duel.get("entry_price") or 0)

        if not coin or entry_price <= 0:
            retries = int(duel.get("retry_count") or 0) + 1
            if retries >= 5 or not coin:
                db.duels.update_one(
                    {"_id": duel["_id"]},
                    {
                        "$set": {
                            "status": "cancelled",
                            "cancel_reason": "delisted" if not coin else "missing_price",
                            "updated_at": now,
                            "retry_count": retries,
                        }
                    },
                )
                db.duel_entries.update_many(
                    {"duel_id": duel["_id"]},
                    {"$set": {"status": "cancelled", "updated_at": now}},
                )
                cancelled += 1
            else:
                db.duels.update_one(
                    {"_id": duel["_id"]},
                    {
                        "$set": {
                            "resolve_at": now + timedelta(hours=6),
                            "retry_count": retries,
                            "updated_at": now,
                        }
                    },
                )
                delayed += 1
            continue

        try:
            exit_price = float(coin.get("current_price") or 0)
        except (TypeError, ValueError):
            exit_price = 0
        if exit_price <= 0:
            retries = int(duel.get("retry_count") or 0) + 1
            if retries >= 5:
                db.duels.update_one(
                    {"_id": duel["_id"]},
                    {
                        "$set": {
                            "status": "cancelled",
                            "cancel_reason": "missing_price",
                            "retry_count": retries,
                            "updated_at": now,
                        }
                    },
                )
                db.duel_entries.update_many(
                    {"duel_id": duel["_id"]},
                    {"$set": {"status": "cancelled", "updated_at": now}},
                )
                cancelled += 1
            else:
                db.duels.update_one(
                    {"_id": duel["_id"]},
                    {
                        "$set": {
                            "resolve_at": now + timedelta(hours=6),
                            "retry_count": retries,
                            "updated_at": now,
                        }
                    },
                )
                delayed += 1
            continue

        move_pct = ((exit_price - entry_price) / entry_price) * 100.0
        if move_pct > 1.0:
            winner = "bull"
        elif move_pct < -1.0:
            winner = "bear"
        else:
            winner = "tie"

        db.duels.update_one(
            {"_id": duel["_id"]},
            {
                "$set": {
                    "status": "resolved",
                    "exit_price": exit_price,
                    "move_pct": move_pct,
                    "winner": winner,
                    "updated_at": now,
                }
            },
        )
        db.duel_entries.update_many(
            {"duel_id": duel["_id"]},
            {"$set": {"status": "resolved", "updated_at": now}},
        )

        # Badge counters on users
        bull_e = db.duel_entries.find_one({"_id": duel["bull_entry_id"]})
        bear_e = db.duel_entries.find_one({"_id": duel["bear_entry_id"]})
        for entry, side in ((bull_e, "bull"), (bear_e, "bear")):
            if not entry:
                continue
            uid = entry["user_id"]
            try:
                uoid = ObjectId(uid)
            except (InvalidId, TypeError):
                continue
            if winner == "tie":
                field = "duel_ties"
            elif winner == side:
                field = "duel_wins"
            else:
                field = "duel_losses"
            db.users.update_one({"_id": uoid}, {"$inc": {field: 1}})

            # Optional: push into conviction ledger for the participant
            try:
                from app.services import conviction_service

                conviction_service.upsert_conviction(
                    uid,
                    coin_id,
                    thesis=entry.get("thesis") or "",
                    side=side,
                    source="duel",
                    entry_price=float(entry.get("entry_price") or entry_price),
                )
            except Exception:
                pass

        resolved += 1

    return {"resolved": resolved, "delayed": delayed, "cancelled": cancelled}
