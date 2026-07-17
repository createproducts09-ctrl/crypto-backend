from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.extensions import db

SECTIONS = [
    "general",
    "market_analysis",
    "altcoins",
    "bitcoin",
    "ethereum",
    "trading",
    "memes",
    "news",
    "ai",
    "portfolio_reviews",
    "technical_analysis",
]


def list_sections() -> list[dict[str, str]]:
    labels = {
        "general": "General Discussion",
        "market_analysis": "Market Analysis",
        "altcoins": "Altcoins",
        "bitcoin": "Bitcoin",
        "ethereum": "Ethereum",
        "trading": "Trading",
        "memes": "Memes",
        "news": "News",
        "ai": "AI Discussions",
        "portfolio_reviews": "Portfolio Reviews",
        "technical_analysis": "Technical Analysis",
    }
    return [{"key": s, "label": labels[s]} for s in SECTIONS]


def list_posts(section: str | None = None, limit: int = 40, user_id: str | None = None) -> list[dict]:
    query: dict[str, Any] = {}
    if section:
        query["section"] = section
    posts = list(db.posts.find(query).sort("created_at", -1).limit(limit))
    return [_serialize_post(p, user_id=user_id) for p in posts]


def create_post(user_id: str, username: str, payload: dict[str, Any]) -> dict:
    doc = {
        "user_id": user_id,
        "username": username,
        "section": payload.get("section") or "general",
        "title": payload.get("title", "").strip(),
        "body": payload.get("body", "").strip(),
        "upvotes": 0,
        "downvotes": 0,
        "score": 0,
        "comment_count": 0,
        "bookmarks": [],
        "followers": [],
        "created_at": datetime.now(timezone.utc),
    }
    res = db.posts.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize_post(doc, user_id=user_id)


def get_post(post_id: str, user_id: str | None = None) -> dict | None:
    post = db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        return None
    comments = list(db.comments.find({"post_id": post_id}).sort("created_at", 1))
    data = _serialize_post(post, user_id=user_id)
    data["comments"] = [
        {
            "id": str(c["_id"]),
            "user_id": c["user_id"],
            "username": c.get("username"),
            "body": c.get("body"),
            "created_at": c.get("created_at").isoformat() if c.get("created_at") else None,
        }
        for c in comments
    ]
    return data


def add_comment(user_id: str, username: str, post_id: str, body: str) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "post_id": post_id,
        "user_id": user_id,
        "username": username,
        "body": body.strip(),
        "created_at": now,
    }
    res = db.comments.insert_one(doc)
    db.posts.update_one({"_id": ObjectId(post_id)}, {"$inc": {"comment_count": 1}})
    return {
        "id": str(res.inserted_id),
        "user_id": user_id,
        "username": username,
        "body": body.strip(),
        "created_at": now.isoformat(),
    }


def vote_post(user_id: str, post_id: str, direction: str) -> dict | None:
    post = db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        return None
    votes = db.votes.find_one({"user_id": user_id, "post_id": post_id})
    delta_up = delta_down = 0

    if votes:
        if votes["direction"] == direction:
            # Toggle off
            if direction == "up":
                delta_up = -1
            else:
                delta_down = -1
            db.votes.delete_one({"_id": votes["_id"]})
        else:
            # Switch vote
            if direction == "up":
                delta_up, delta_down = 1, -1
            else:
                delta_up, delta_down = -1, 1
            db.votes.update_one({"_id": votes["_id"]}, {"$set": {"direction": direction}})
    else:
        if direction == "up":
            delta_up = 1
        else:
            delta_down = 1
        db.votes.insert_one({"user_id": user_id, "post_id": post_id, "direction": direction})

    db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$inc": {"upvotes": delta_up, "downvotes": delta_down, "score": delta_up - delta_down}},
    )
    post = db.posts.find_one({"_id": ObjectId(post_id)})
    return _serialize_post(post, user_id=user_id)


def bookmark_post(user_id: str, post_id: str) -> dict | None:
    post = db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        return None
    bookmarks = post.get("bookmarks") or []
    if user_id in bookmarks:
        db.posts.update_one({"_id": ObjectId(post_id)}, {"$pull": {"bookmarks": user_id}})
    else:
        db.posts.update_one({"_id": ObjectId(post_id)}, {"$addToSet": {"bookmarks": user_id}})
    post = db.posts.find_one({"_id": ObjectId(post_id)})
    return _serialize_post(post, user_id=user_id)


def _serialize_post(post: dict, user_id: str | None = None) -> dict:
    user_vote = None
    liked = False
    if user_id:
        vote = db.votes.find_one({"user_id": user_id, "post_id": str(post["_id"])})
        if vote:
            user_vote = vote.get("direction")
        liked = user_id in (post.get("bookmarks") or [])

    return {
        "id": str(post["_id"]),
        "user_id": post.get("user_id"),
        "username": post.get("username"),
        "section": post.get("section"),
        "title": post.get("title"),
        "body": post.get("body"),
        "upvotes": post.get("upvotes", 0),
        "downvotes": post.get("downvotes", 0),
        "score": post.get("score", 0),
        "comment_count": post.get("comment_count", 0),
        "like_count": len(post.get("bookmarks") or []),
        "user_vote": user_vote,
        "liked": liked,
        "created_at": post.get("created_at").isoformat() if post.get("created_at") else None,
    }
