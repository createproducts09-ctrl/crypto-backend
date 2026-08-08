from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services import admin_service

bp = Blueprint("admin", __name__)


def _key_from_request() -> str | None:
    header = request.headers.get("X-Admin-Key")
    if header:
        return header
    body = request.get_json(silent=True) or {}
    return body.get("admin_key") or request.args.get("admin_key")


@bp.post("/unlock")
def unlock():
    """Validate admin key — used by the key gate page."""
    try:
        admin_service.assert_admin_key(_key_from_request())
    except PermissionError as exc:
        return jsonify({"error": str(exc), "code": "forbidden"}), 403
    return jsonify({"ok": True})


@bp.get("/users")
def users():
    try:
        admin_service.assert_admin_key(_key_from_request())
    except PermissionError as exc:
        return jsonify({"error": str(exc), "code": "forbidden"}), 403

    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    try:
        skip = int(request.args.get("skip", 0))
    except ValueError:
        skip = 0
    q = request.args.get("q") or None
    return jsonify(admin_service.list_users(limit=limit, skip=skip, q=q))


@bp.get("/stats")
def stats():
    try:
        admin_service.assert_admin_key(_key_from_request())
    except PermissionError as exc:
        return jsonify({"error": str(exc), "code": "forbidden"}), 403

    data = admin_service.list_users(limit=1, skip=0)
    return jsonify({"total_users": data["total"]})
