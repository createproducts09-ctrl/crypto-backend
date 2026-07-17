from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from bson import ObjectId
from flask_jwt_extended import create_access_token, create_refresh_token

from app.clients import resend_mail
from app.extensions import db

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def serialize_user(user: dict[str, Any]) -> dict[str, Any]:
    # Legacy users without the field are treated as verified
    verified = user.get("email_verified")
    if verified is None:
        verified = True
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "username": user["username"],
        "avatar": user.get("avatar"),
        "preferences": user.get("preferences") or {"theme": "system", "notifications": True},
        "email_verified": bool(verified),
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
    }


def _tokens_for(user_id: ObjectId | str) -> dict[str, str]:
    identity = str(user_id)
    return {
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
    }


def register_user(email: str, password: str, username: str) -> tuple[dict | None, str | None]:
    email = email.strip().lower()
    username = username.strip()
    if not email or "@" not in email:
        return None, "Valid email required"
    if not username or len(username) < 2:
        return None, "Username must be at least 2 characters"
    if not password or len(password) < 6:
        return None, "Password must be at least 6 characters"
    if db.users.find_one({"$or": [{"email": email}, {"username": username}]}):
        return None, "Email or username already exists"

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    code = _make_code()
    now = _now()
    doc = {
        "email": email,
        "username": username,
        "password_hash": password_hash,
        "avatar": None,
        "preferences": {"theme": "system", "notifications": True},
        "reading_history": [],
        "achievements": ["joined"],
        "email_verified": False,
        "email_verification_code": code,
        "email_verification_expires": now + timedelta(hours=24),
        "welcome_email_sent": False,
        "created_at": now,
        "updated_at": now,
    }
    result = db.users.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Verification email (required for confirming the address)
    verify_result = resend_mail.send_verification_email(email, username, code)
    # Welcome / gratitude email for downloading the app
    welcome_result = resend_mail.send_welcome_email(email, username)
    if welcome_result.get("ok"):
        db.users.update_one({"_id": result.inserted_id}, {"$set": {"welcome_email_sent": True}})

    tokens = _tokens_for(result.inserted_id)
    return {
        "user": serialize_user(doc),
        **tokens,
        "email_sent": bool(verify_result.get("ok")),
        "welcome_sent": bool(welcome_result.get("ok")),
        "needs_verification": True,
        "mail_error": None
        if verify_result.get("ok")
        else (verify_result.get("error") if not verify_result.get("skipped") else None),
    }, None


def login_user(email: str, password: str) -> tuple[dict | None, str | None]:
    user = db.users.find_one({"email": email.strip().lower()})
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return None, "Invalid credentials"
    tokens = _tokens_for(user["_id"])
    verified = user.get("email_verified")
    if verified is None:
        verified = True
    return {
        "user": serialize_user(user),
        **tokens,
        "needs_verification": not bool(verified),
    }, None


def verify_email(user_id: str | None, email: str | None, code: str) -> tuple[dict | None, str | None]:
    code = (code or "").strip()
    if not code or len(code) != 6 or not code.isdigit():
        return None, "Enter the 6-digit code from your email"

    user = None
    if user_id:
        try:
            user = db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            user = None
    if not user and email:
        user = db.users.find_one({"email": email.strip().lower()})
    if not user:
        return None, "Account not found"

    if user.get("email_verified"):
        return {"user": serialize_user(user), "already_verified": True}, None

    stored = (user.get("email_verification_code") or "").strip()
    expires = user.get("email_verification_expires")
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if not stored or stored != code:
        return None, "Invalid verification code"
    if expires and expires < _now():
        return None, "Code expired — request a new one"

    db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "email_verified": True,
                "updated_at": _now(),
            },
            "$unset": {
                "email_verification_code": "",
                "email_verification_expires": "",
            },
        },
    )
    user = db.users.find_one({"_id": user["_id"]})

    # Ensure welcome mail went out (idempotent)
    if user and not user.get("welcome_email_sent"):
        welcome = resend_mail.send_welcome_email(user["email"], user["username"])
        if welcome.get("ok"):
            db.users.update_one({"_id": user["_id"]}, {"$set": {"welcome_email_sent": True}})

    return {"user": serialize_user(user), "already_verified": False}, None


def resend_verification(user_id: str | None, email: str | None) -> tuple[dict | None, str | None]:
    user = None
    if user_id:
        try:
            user = db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            user = None
    if not user and email:
        user = db.users.find_one({"email": email.strip().lower()})
    if not user:
        return None, "Account not found"
    if user.get("email_verified"):
        return {"ok": True, "already_verified": True}, None

    # Rate limit: don't resend more than once per 60s
    last = user.get("email_verification_sent_at")
    if last:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if _now() - last < timedelta(seconds=60):
            return None, "Please wait a minute before requesting another code"

    code = _make_code()
    now = _now()
    db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "email_verification_code": code,
                "email_verification_expires": now + timedelta(hours=24),
                "email_verification_sent_at": now,
                "updated_at": now,
            }
        },
    )
    result = resend_mail.send_verification_email(user["email"], user["username"], code)
    if not result.get("ok") and not result.get("skipped"):
        return None, f"Could not send email: {result.get('error')}"
    if result.get("skipped"):
        return None, "Email service is not configured"
    return {"ok": True, "email": user["email"]}, None


def get_user(user_id: str) -> dict | None:
    try:
        user = db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None
    return serialize_user(user) if user else None


def update_user(user_id: str, patch: dict[str, Any]) -> dict | None:
    allowed = {"username", "avatar", "preferences"}
    data = {k: v for k, v in patch.items() if k in allowed}
    data["updated_at"] = _now()
    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": data})
    return get_user(user_id)


def delete_account(user_id: str) -> tuple[bool, str | None]:
    """Permanently remove the user and associated personal app data (Play Store requirement)."""
    try:
        oid = ObjectId(user_id)
    except Exception:
        return False, "Invalid user"

    user = db.users.find_one({"_id": oid})
    if not user:
        return False, "Account not found"

    uid = str(oid)

    db.watchlist.delete_many({"user_id": uid})
    db.portfolio.delete_many({"user_id": uid})
    db.baskets.delete_many({"user_id": uid})
    db.discover_swipes.delete_many({"user_id": uid})
    db.convictions.delete_many({"user_id": uid})
    db.quiet_prefs.delete_many({"user_id": uid})
    db.cooling_bag.delete_many({"user_id": uid})
    db.duel_entries.delete_many({"user_id": uid})
    try:
        db.alerts.delete_many({"user_id": uid})
    except Exception:
        pass
    try:
        db.ai_threads.delete_many({"user_id": uid})
        db.ai_messages.delete_many({"user_id": uid})
    except Exception:
        pass

    # Anonymize public community content
    db.posts.update_many(
        {"user_id": uid},
        {"$set": {"username": "deleted", "user_id": f"deleted:{uid}", "body": "[deleted]"}},
    )
    try:
        db.comments.update_many(
            {"user_id": uid},
            {"$set": {"username": "deleted", "user_id": f"deleted:{uid}", "body": "[deleted]"}},
        )
    except Exception:
        pass

    db.users.delete_one({"_id": oid})
    return True, None
