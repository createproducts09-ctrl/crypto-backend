from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.extensions import db


def list_alerts(user_id: str) -> list[dict]:
    alerts = list(db.alerts.find({"user_id": user_id}).sort("created_at", -1))
    return [
        {
            "id": str(a["_id"]),
            "type": a.get("type"),
            "coin_id": a.get("coin_id"),
            "condition": a.get("condition"),
            "target": a.get("target"),
            "active": a.get("active", True),
            "triggered": a.get("triggered", False),
            "message": a.get("message"),
            "created_at": a.get("created_at").isoformat() if a.get("created_at") else None,
        }
        for a in alerts
    ]


def create_alert(user_id: str, payload: dict[str, Any]) -> dict:
    doc = {
        "user_id": user_id,
        "type": payload.get("type", "price"),
        "coin_id": payload.get("coin_id"),
        "condition": payload.get("condition", "above"),
        "target": float(payload.get("target") or 0),
        "active": True,
        "triggered": False,
        "message": payload.get("message"),
        "created_at": datetime.now(timezone.utc),
    }
    res = db.alerts.insert_one(doc)
    return {"id": str(res.inserted_id), **{k: v for k, v in doc.items() if k != "created_at"}}


def delete_alert(user_id: str, alert_id: str) -> bool:
    res = db.alerts.delete_one({"_id": ObjectId(alert_id), "user_id": user_id})
    return res.deleted_count > 0


def evaluate_all_alerts() -> int:
    triggered = 0
    for alert in db.alerts.find({"active": True, "triggered": False, "type": "price"}):
        coin = db.coins.find_one({"id": alert.get("coin_id")})
        if not coin:
            continue
        price = float(coin.get("current_price") or 0)
        target = float(alert.get("target") or 0)
        cond = alert.get("condition")
        hit = (cond == "above" and price >= target) or (cond == "below" and price <= target)
        if hit:
            db.alerts.update_one(
                {"_id": alert["_id"]},
                {
                    "$set": {
                        "triggered": True,
                        "active": False,
                        "triggered_at": datetime.now(timezone.utc),
                        "message": f"{alert.get('coin_id')} hit {price}",
                    }
                },
            )
            triggered += 1
    return triggered
