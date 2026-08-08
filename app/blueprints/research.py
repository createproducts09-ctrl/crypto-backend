from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request

from app.services import monitor_service, research_service

bp = Blueprint("research", __name__)


def _optional_user_id() -> str | None:
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:
        return None


@bp.get("/watchlist/feed")
@jwt_required()
def watchlist_feed():
    return jsonify(monitor_service.watchlist_changes(get_jwt_identity()))


@bp.get("/thesis/<basket_id>")
@jwt_required()
def thesis_health(basket_id: str):
    data = monitor_service.thesis_health_for_basket(get_jwt_identity(), basket_id)
    if not data:
        return jsonify({"error": "Thesis not found"}), 404
    return jsonify(data)


@bp.post("/compare")
def compare():
    body = request.get_json() or {}
    ids = body.get("coin_ids") or []
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.split(",") if x.strip()]
    if len(ids) < 2:
        return jsonify({"error": "Provide at least 2 coin_ids"}), 400
    return jsonify(research_service.compare_coins(ids))


@bp.post("/investigate")
def investigate():
    """Investigator AI grounded in research objects."""
    body = request.get_json() or {}
    question = (body.get("question") or body.get("content") or "").strip()
    coin_id = (body.get("coin_id") or "").strip() or None
    coin_ids = body.get("coin_ids") or []
    if isinstance(coin_ids, str):
        coin_ids = [x.strip() for x in coin_ids.split(",") if x.strip()]
    try:
        result = research_service.investigator_answer(question, coin_id=coin_id, coin_ids=coin_ids)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/<coin_id>")
def get_research(coin_id: str):
    """Full research pack: score, signals, so-what, thesis."""
    force = str(request.args.get("force", "")).lower() in {"1", "true", "yes"}
    with_ai = str(request.args.get("ai", "1")).lower() not in {"0", "false", "no"}
    data = research_service.full_research(coin_id, force=force, with_ai=with_ai)
    if not data:
        return jsonify({"error": "Coin not found"}), 404
    user_id = _optional_user_id()
    # Diff against prior view BEFORE recording this visit
    data["since_last_check"] = monitor_service.what_changed(coin_id, user_id=user_id)
    if user_id:
        monitor_service.mark_viewed(user_id, coin_id)
    if hasattr(data.get("updated_at"), "isoformat"):
        data["updated_at"] = data["updated_at"].isoformat()
    return jsonify(data)


@bp.get("/<coin_id>/score")
def get_score(coin_id: str):
    pack = research_service.get_or_compute(coin_id)
    if not pack:
        return jsonify({"error": "Coin not found"}), 404
    return jsonify(
        {
            "coin_id": coin_id,
            "research_score": pack.get("research_score"),
            "categories": pack.get("categories"),
            "traffic_lights": pack.get("traffic_lights"),
            "score_rationale": pack.get("score_rationale"),
            "why_interesting": pack.get("why_interesting"),
            "biggest_concern": pack.get("biggest_concern"),
            "signals": pack.get("signals"),
        }
    )


@bp.get("/<coin_id>/changes")
def get_changes(coin_id: str):
    user_id = _optional_user_id()
    try:
        days = min(int(request.args.get("days", 7)), 90)
    except ValueError:
        days = 7
    return jsonify(monitor_service.what_changed(coin_id, user_id=user_id, since_days=days))


@bp.post("/<coin_id>/viewed")
@jwt_required()
def mark_viewed(coin_id: str):
    return jsonify(monitor_service.mark_viewed(get_jwt_identity(), coin_id))
