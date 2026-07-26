from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request

from app.services import discover_service

bp = Blueprint("discover", __name__)


def _optional_user_id() -> str | None:
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:
        return None


@bp.get("/filters")
def filters():
    user_id = _optional_user_id()
    return jsonify({"items": discover_service.list_filters(user_id=user_id)})


@bp.get("/deck")
def deck():
    from app.services import billing_service

    filter_key = request.args.get("filter", "trending")
    try:
        limit = min(int(request.args.get("limit", 30)), 50)
    except ValueError:
        limit = 30
    allow_recycle = str(request.args.get("recycle", "")).lower() in {"1", "true", "yes"}
    # Browse mode (Explore): ignore swipe exclusions so markets stay browsable
    browse = str(request.args.get("browse", "")).lower() in {"1", "true", "yes"}
    exclude_raw = request.args.get("exclude") or ""
    exclude_ids = [x.strip() for x in exclude_raw.split(",") if x.strip()]
    # Always resolve optional auth so plan gates / why-blurbs work in browse mode
    auth_user_id = _optional_user_id()
    user_id = None if browse else auth_user_id
    try:
        billing_service.assert_can_use_filter(auth_user_id, filter_key)
    except PermissionError as exc:
        return jsonify({"error": str(exc), "code": "plan_limit", "upgrade": True}), 402
    data = discover_service.get_deck(
        filter_key=filter_key,
        limit=limit,
        user_id=user_id,
        exclude_ids=None if browse else (exclude_ids or None),
        allow_recycle=False if browse else allow_recycle,
        include_why=bool(auth_user_id and billing_service.is_keel(auth_user_id)),
    )
    if browse:
        data.setdefault("meta", {})["browse"] = True
        data["meta"]["unique"] = False
    return jsonify(data)


@bp.post("/swipe")
@jwt_required()
def swipe():
    body = request.get_json() or {}
    coin_id = (body.get("coin_id") or "").strip()
    action = (body.get("action") or "").strip().lower()
    try:
        result = discover_service.record_swipe(get_jwt_identity(), coin_id, action)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/allow-passed")
@jwt_required()
def allow_passed():
    """Clear pass history (all or selected) so rejected coins can appear again."""
    body = request.get_json() or {}
    coin_ids = body.get("coin_ids")
    if coin_ids is not None and not isinstance(coin_ids, list):
        return jsonify({"error": "coin_ids must be a list"}), 400
    result = discover_service.allow_passed_again(get_jwt_identity(), coin_ids)
    return jsonify(result)


@bp.get("/stats")
@jwt_required()
def stats():
    return jsonify(discover_service.get_stats(get_jwt_identity()))


@bp.get("/pulse")
@jwt_required()
def pulse():
    """Keel-only crowd pulse: most passed / interested / watchlisted coins."""
    from app.services import billing_service

    user_id = get_jwt_identity()
    if not billing_service.is_keel(user_id):
        return jsonify(
            {
                "error": "Swipe Pulse is a Keel feature — see what the desk is passing, liking, and watching.",
                "code": "plan_limit",
                "upgrade": True,
            }
        ), 402
    try:
        limit = min(int(request.args.get("limit", 12)), 25)
    except ValueError:
        limit = 12
    return jsonify(discover_service.get_pulse(limit=limit))
