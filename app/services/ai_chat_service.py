from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.clients.ai import ai_service
from app.extensions import db


def list_threads(user_id: str) -> list[dict]:
    threads = list(db.ai_threads.find({"user_id": user_id}).sort("updated_at", -1).limit(50))
    return [
        {
            "id": str(t["_id"]),
            "title": t.get("title") or "New chat",
            "coin_id": t.get("coin_id"),
            "updated_at": t.get("updated_at").isoformat() if t.get("updated_at") else None,
        }
        for t in threads
    ]


def get_thread(user_id: str, thread_id: str) -> dict | None:
    thread = db.ai_threads.find_one({"_id": ObjectId(thread_id), "user_id": user_id})
    if not thread:
        return None
    messages = list(db.ai_messages.find({"thread_id": thread_id}).sort("created_at", 1))
    return {
        "id": str(thread["_id"]),
        "title": thread.get("title"),
        "coin_id": thread.get("coin_id"),
        "messages": [
            {
                "id": str(m["_id"]),
                "role": m["role"],
                "content": m["content"],
                "created_at": m.get("created_at").isoformat() if m.get("created_at") else None,
            }
            for m in messages
        ],
    }


def delete_thread(user_id: str, thread_id: str) -> bool:
    try:
        oid = ObjectId(thread_id)
    except Exception:
        return False
    result = db.ai_threads.delete_one({"_id": oid, "user_id": user_id})
    if result.deleted_count == 0:
        return False
    db.ai_messages.delete_many({"thread_id": thread_id})
    return True


def send_message(user_id: str, payload: dict[str, Any]) -> dict:
    thread_id = payload.get("thread_id")
    content = (payload.get("content") or "").strip()
    coin_id = payload.get("coin_id")
    now = datetime.now(timezone.utc)

    if thread_id:
        thread = db.ai_threads.find_one({"_id": ObjectId(thread_id), "user_id": user_id})
        if not thread:
            raise ValueError("Thread not found")
    else:
        title = content[:48] + ("…" if len(content) > 48 else "")
        res = db.ai_threads.insert_one(
            {
                "user_id": user_id,
                "title": title or "New chat",
                "coin_id": coin_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        thread_id = str(res.inserted_id)

    db.ai_messages.insert_one(
        {"thread_id": thread_id, "role": "user", "content": content, "created_at": now}
    )

    history = list(db.ai_messages.find({"thread_id": thread_id}).sort("created_at", 1).limit(40))
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    context_parts = []
    research_mode = False
    if coin_id:
        coin = db.coins.find_one({"id": coin_id}, {"_id": 0})
        if coin:
            research_mode = True
            risk = coin.get("risk") or {}
            context_parts.append(
                "Attached coin research context (use this as ground truth; do not invent prices):\n"
                f"- id={coin.get('id')} name={coin.get('name')} symbol={coin.get('symbol')}\n"
                f"- price_usd={coin.get('current_price')} rank={coin.get('market_cap_rank')}\n"
                f"- mcap={coin.get('market_cap')} fdv={coin.get('fully_diluted_valuation')} "
                f"volume_24h={coin.get('total_volume')}\n"
                f"- change_1h={coin.get('price_change_percentage_1h')} "
                f"change_24h={coin.get('price_change_percentage_24h')} "
                f"change_7d={coin.get('price_change_percentage_7d')} "
                f"change_30d={coin.get('price_change_percentage_30d')}\n"
                f"- ath={coin.get('ath')} atl={coin.get('atl')}\n"
                f"- circulating={coin.get('circulating_supply')} "
                f"total={coin.get('total_supply')} max={coin.get('max_supply')}\n"
                f"- sentiment={coin.get('sentiment')} risk={risk.get('level')} "
                f"risk_confidence={risk.get('confidence')} "
                f"community_score={coin.get('community_score')} "
                f"liquidity_score={coin.get('liquidity_score')}\n"
                f"- categories={', '.join((coin.get('categories') or coin.get('tags') or [])[:8])}\n"
                f"- insight={coin.get('ai_insight')}\n"
                f"- about={'; '.join((coin.get('about_bullets') or [])[:4]) or (coin.get('description') or '')[:500]}"
            )
            # Pull latest cached chart TA if present in a recent call is hard; keep market snapshot rich.
    news = list(db.news.find({}, {"_id": 0, "title": 1, "sentiment": 1}).sort("published_at", -1).limit(5))
    if news:
        context_parts.append("Recent headlines: " + "; ".join(n.get("title", "") for n in news))

    reply = ai_service.chat(
        messages,
        context="\n".join(context_parts) or None,
        research_mode=research_mode,
    )
    db.ai_messages.insert_one(
        {
            "thread_id": thread_id,
            "role": "assistant",
            "content": reply,
            "created_at": datetime.now(timezone.utc),
        }
    )
    db.ai_threads.update_one({"_id": ObjectId(thread_id)}, {"$set": {"updated_at": datetime.now(timezone.utc)}})
    return {"thread_id": thread_id, "reply": reply}
