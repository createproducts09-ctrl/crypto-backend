from __future__ import annotations

import re
from datetime import datetime

from app.clients.coingecko import coingecko
from app.extensions import db


def _safe_regex(query: str) -> str:
    return re.escape((query or "").strip())


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return None


def global_search(query: str, limit: int = 12) -> dict:
    q = (query or "").strip()
    if not q:
        return {"coins": [], "posts": [], "news": [], "users": []}

    pattern = _safe_regex(q)

    coins = list(
        db.coins.find(
            {
                "$or": [
                    {"name": {"$regex": pattern, "$options": "i"}},
                    {"symbol": {"$regex": pattern, "$options": "i"}},
                    {"id": {"$regex": pattern, "$options": "i"}},
                ]
            },
            {"_id": 0},
        ).limit(limit)
    )
    if len(coins) < 3:
        try:
            remote = coingecko.search(q)
            for c in (remote.get("coins") or [])[:limit]:
                cid = c.get("id")
                if not cid:
                    continue
                if any(x.get("id") == cid for x in coins):
                    continue
                coins.append(
                    {
                        "id": cid,
                        "name": c.get("name"),
                        "symbol": (c.get("symbol") or "").upper(),
                        "market_cap_rank": c.get("market_cap_rank"),
                        "image": (c.get("large") or c.get("thumb")),
                    }
                )
        except Exception:
            pass

    posts = list(
        db.posts.find(
            {
                "$or": [
                    {"title": {"$regex": pattern, "$options": "i"}},
                    {"body": {"$regex": pattern, "$options": "i"}},
                ]
            }
        )
        .sort("created_at", -1)
        .limit(limit)
    )
    news = list(
        db.news.find(
            {
                "$or": [
                    {"title": {"$regex": pattern, "$options": "i"}},
                    {"body": {"$regex": pattern, "$options": "i"}},
                ]
            },
            {"_id": 0},
        )
        .sort("published_at", -1)
        .limit(limit)
    )
    users = list(
        db.users.find(
            {"username": {"$regex": pattern, "$options": "i"}},
            {"password_hash": 0},
        ).limit(limit)
    )

    return {
        "coins": [c for c in coins if c.get("id")][:limit],
        "posts": [
            {
                "id": str(p["_id"]),
                "title": p.get("title"),
                "section": p.get("section"),
                "username": p.get("username"),
            }
            for p in posts
        ],
        "news": [
            {
                **n,
                "published_at": _iso(n.get("published_at")),
            }
            for n in news
            if n.get("external_id") or n.get("title")
        ],
        "users": [
            {"id": str(u["_id"]), "username": u.get("username"), "avatar": u.get("avatar")}
            for u in users
        ],
    }
