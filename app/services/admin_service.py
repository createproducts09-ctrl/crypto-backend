from __future__ import annotations

import hmac
import os
from typing import Any

from app.extensions import db
from app.services.auth_service import serialize_user


def admin_key() -> str:
    return (os.getenv("ADMIN_KEY") or "").strip()


def assert_admin_key(provided: str | None) -> None:
    expected = admin_key()
    if not expected:
        raise PermissionError("Admin key is not configured on the server")
    got = (provided or "").strip()
    if not got or not hmac.compare_digest(got, expected):
        raise PermissionError("Invalid admin key")


def list_users(*, limit: int = 100, skip: int = 0, q: str | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    skip = max(0, int(skip))
    query: dict[str, Any] = {}
    if q:
        term = q.strip()
        if term:
            query["$or"] = [
                {"email": {"$regex": term, "$options": "i"}},
                {"username": {"$regex": term, "$options": "i"}},
                {"display_name": {"$regex": term, "$options": "i"}},
            ]

    total = db.users.count_documents({})
    filtered = db.users.count_documents(query) if query else total
    cursor = (
        db.users.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    items = [serialize_user(u) for u in cursor]
    return {
        "total": total,
        "filtered": filtered,
        "limit": limit,
        "skip": skip,
        "items": items,
    }
