from __future__ import annotations

import logging
import secrets
import threading
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
    plan = (user.get("plan") or "free").strip().lower()
    if plan not in {"free", "keel"}:
        plan = "free"
    fortune = user.get("fortune_pick") or None
    fortune_out = None
    if isinstance(fortune, dict) and fortune.get("coin_id"):
        picked_at = fortune.get("picked_at")
        fortune_out = {
            "coin_id": str(fortune["coin_id"]),
            "picked_at": picked_at.isoformat() if hasattr(picked_at, "isoformat") else picked_at,
        }
    return {
        "id": str(user["_id"]),
        "email": user.get("email") or "",
        "username": user["username"],
        "display_name": user.get("display_name") or None,
        "avatar": user.get("avatar"),
        "bio": user.get("bio") or None,
        "preferences": user.get("preferences") or {"theme": "system", "notifications": True},
        "email_verified": bool(verified),
        "plan": plan,
        "fortune_pick": fortune_out,
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
    }


def _tokens_for(user_id: ObjectId | str) -> dict[str, str]:
    identity = str(user_id)
    return {
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
    }


def _send_emails_async(app, email: str, username: str, code: str):
    """Send verification and welcome emails in background thread."""
    try:
        with app.app_context():
            verify_result = resend_mail.send_verification_email(email, username, code)
            welcome_result = resend_mail.send_welcome_email(email, username)
        logger.info(
            "Background emails sent for %s: verify=%s, welcome=%s",
            email,
            verify_result.get("ok"),
            welcome_result.get("ok"),
        )
    except Exception as exc:
        logger.warning("Background email sending failed for %s: %s", email, exc)


def register_user(email: str, password: str, username: str) -> tuple[dict | None, str | None]:
    from flask import current_app

    email = email.strip().lower()
    username = username.strip()
    if not email or "@" not in email:
        return None, "Valid email required"
    if not username or len(username) < 2:
        return None, "Username must be at least 2 characters"
    if not password or len(password) < 6:
        return None, "Password must be at least 6 characters"
    if db.users.find_one({"email": email}):
        return None, "An account with this email already exists"
    if db.users.find_one({"username": username}):
        return None, "This username is already taken"

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
        "plan": "free",
        "created_at": now,
        "updated_at": now,
    }
    result = db.users.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Send emails asynchronously to avoid blocking the response
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_send_emails_async,
        args=(app, email, username, code),
        daemon=True,
    )
    thread.start()

    tokens = _tokens_for(result.inserted_id)
    return {
        "user": serialize_user(doc),
        **tokens,
        "email_sent": True,  # Optimistically true since we're sending in background
        "welcome_sent": True,
        "needs_verification": True,
        "mail_error": None,
    }, None


def login_user(email: str, password: str) -> tuple[dict | None, str | None]:
    user = db.users.find_one({"email": email.strip().lower()})
    pw_hash = (user or {}).get("password_hash") if user else None
    if not user or not pw_hash:
        return None, "Invalid credentials"
    try:
        ok = bcrypt.checkpw(password.encode(), pw_hash.encode())
    except Exception:
        ok = False
    if not ok:
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


def _unique_username(base: str) -> str:
    import re

    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "", (base or "").strip())[:24]
    if len(cleaned) < 2:
        cleaned = "user"
    candidate = cleaned
    n = 0
    while db.users.find_one({"username": candidate}):
        n += 1
        suffix = str(n)
        candidate = f"{cleaned[: max(1, 32 - len(suffix))]}{suffix}"
    return candidate


def login_with_google(id_token: str) -> tuple[dict | None, str | None]:
    """Verify Google ID token and return app JWT session (create user if needed)."""
    from flask import current_app
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    token = (id_token or "").strip()
    if not token:
        return None, "Google credential required"

    client_id = (current_app.config.get("GOOGLE_CLIENT_ID") or "").strip()
    if not client_id:
        return None, "Google sign-in is not configured"

    try:
        info = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=client_id,
        )
    except Exception as exc:
        logger.warning("Google token verify failed: %s", exc)
        return None, "Invalid Google credential"

    if info.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        return None, "Invalid Google credential"

    sub = str(info.get("sub") or "").strip()
    email = str(info.get("email") or "").strip().lower()
    if not sub or not email or "@" not in email:
        return None, "Google account email is required"
    if not info.get("email_verified", False):
        return None, "Google email is not verified"

    picture = info.get("picture")
    name = (info.get("name") or info.get("given_name") or "").strip() or None
    now = _now()

    user = db.users.find_one({"google_sub": sub}) or db.users.find_one({"email": email})
    if user:
        updates: dict[str, Any] = {"updated_at": now, "auth_provider": "google"}
        if not user.get("google_sub"):
            updates["google_sub"] = sub
        if not user.get("email_verified"):
            updates["email_verified"] = True
        if picture and not user.get("avatar"):
            updates["avatar"] = picture
        if name and not user.get("display_name"):
            updates["display_name"] = name
        if updates:
            db.users.update_one({"_id": user["_id"]}, {"$set": updates})
            user = db.users.find_one({"_id": user["_id"]})
    else:
        local = email.split("@", 1)[0]
        username = _unique_username(local)
        doc = {
            "email": email,
            "username": username,
            "password_hash": None,
            "google_sub": sub,
            "auth_provider": "google",
            "display_name": name,
            "avatar": picture if isinstance(picture, str) else None,
            "preferences": {"theme": "system", "notifications": True},
            "reading_history": [],
            "achievements": ["joined"],
            "email_verified": True,
            "welcome_email_sent": False,
            "plan": "free",
            "created_at": now,
            "updated_at": now,
        }
        result = db.users.insert_one(doc)
        doc["_id"] = result.inserted_id
        user = doc

        # Welcome email in background (no verification needed)
        try:
            from flask import current_app as flask_app

            app = flask_app._get_current_object()

            def _welcome():
                try:
                    with app.app_context():
                        resend_mail.send_welcome_email(email, username)
                        db.users.update_one(
                            {"_id": result.inserted_id},
                            {"$set": {"welcome_email_sent": True}},
                        )
                except Exception as exc:
                    logger.warning("Google welcome email failed for %s: %s", email, exc)

            threading.Thread(target=_welcome, daemon=True).start()
        except Exception:
            pass

    tokens = _tokens_for(user["_id"])
    return {
        "user": serialize_user(user),
        **tokens,
        "needs_verification": False,
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


def claim_fortune(user_id: str, coin_id: str) -> tuple[dict | None, str | None]:
    """One lifetime pick per account. Idempotent — returns existing pick if already claimed."""
    from pymongo import ReturnDocument

    coin_id = (coin_id or "").strip()
    if not coin_id or len(coin_id) > 64:
        return None, "Invalid coin"
    try:
        oid = ObjectId(user_id)
    except Exception:
        return None, "Invalid user"

    user = db.users.find_one({"_id": oid})
    if not user:
        return None, "Not found"

    existing = user.get("fortune_pick")
    if isinstance(existing, dict) and existing.get("coin_id"):
        return serialize_user(user), None

    now = _now()
    updated = db.users.find_one_and_update(
        {"_id": oid, "fortune_pick": {"$exists": False}},
        {
            "$set": {
                "fortune_pick": {"coin_id": coin_id, "picked_at": now},
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        user = db.users.find_one({"_id": oid})
        return serialize_user(user) if user else None, None
    return serialize_user(updated), None


def update_user(user_id: str, patch: dict[str, Any]) -> dict | None:
    allowed = {"username", "avatar", "preferences", "display_name", "bio"}
    data = {k: v for k, v in patch.items() if k in allowed}
    if "username" in data:
        username = str(data["username"] or "").strip()
        if len(username) < 2:
            raise ValueError("Username must be at least 2 characters")
        if len(username) > 32:
            raise ValueError("Username is too long")
        if not all(c.isalnum() or c in "._-" for c in username):
            raise ValueError("Username can only use letters, numbers, . _ -")
        taken = db.users.find_one(
            {"username": username, "_id": {"$ne": ObjectId(user_id)}}
        )
        if taken:
            raise ValueError("Username is already taken")
        data["username"] = username
    if "display_name" in data:
        display = str(data["display_name"] or "").strip()
        if len(display) > 48:
            raise ValueError("Nickname is too long (max 48)")
        data["display_name"] = display or None
    if "bio" in data:
        bio = str(data["bio"] or "").strip()
        if len(bio) > 160:
            raise ValueError("Bio is too long (max 160)")
        data["bio"] = bio or None
    if "avatar" in data:
        avatar = data["avatar"]
        if avatar is None or avatar == "":
            data["avatar"] = None
        else:
            avatar = str(avatar).strip()
            if avatar.startswith("data:image/"):
                # ~450KB decoded ≈ ~600k chars base64
                if len(avatar) > 700_000:
                    raise ValueError("Image is too large — try a smaller photo")
                if not any(
                    avatar.startswith(f"data:image/{fmt}")
                    for fmt in ("jpeg", "jpg", "png", "webp", "gif")
                ):
                    raise ValueError("Use a JPEG, PNG, or WebP image")
            elif avatar.startswith("https://"):
                if len(avatar) > 2048:
                    raise ValueError("Avatar URL is too long")
            else:
                raise ValueError("Invalid avatar")
            data["avatar"] = avatar
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
