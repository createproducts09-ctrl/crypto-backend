"""MVP billing endpoints — plans list, entitlements, mock upgrade."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import billing_service

bp = Blueprint("billing", __name__)


@bp.get("/plans")
def plans():
    return jsonify({"items": billing_service.list_plans()})


@bp.get("/entitlements")
@jwt_required()
def entitlements():
    return jsonify(billing_service.entitlements_for(get_jwt_identity()))


@bp.post("/upgrade")
@jwt_required()
def upgrade():
    """Mock upgrade — no Stripe yet. Sets plan on the user document."""
    data = request.get_json() or {}
    plan = (data.get("plan") or "keel").strip().lower()
    if plan not in billing_service.PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    try:
        ent = billing_service.upgrade_user(get_jwt_identity(), plan)
        from app.services import auth_service

        user = auth_service.get_user(get_jwt_identity())
        return jsonify({"ok": True, "entitlements": ent, "user": user})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
