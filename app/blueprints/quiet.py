from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import quiet_service

bp = Blueprint("quiet", __name__)


@bp.get("/prefs")
@jwt_required()
def get_prefs():
    return jsonify(quiet_service.get_prefs(get_jwt_identity()))


@bp.put("/prefs")
@jwt_required()
def put_prefs():
    body = request.get_json() or {}
    try:
        return jsonify(quiet_service.update_prefs(get_jwt_identity(), body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/cooling-bag")
@jwt_required()
def cooling_bag():
    return jsonify({"items": quiet_service.list_cooling_bag(get_jwt_identity())})


@bp.post("/cooling-bag")
@jwt_required()
def add_cooling():
    body = request.get_json() or {}
    try:
        item = quiet_service.add_to_cooling_bag(
            get_jwt_identity(),
            coin_id=body.get("coin_id") or "",
            cool_hours=body.get("cool_hours"),
        )
        return jsonify(item), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/cooling-bag/<coin_id>/release")
@jwt_required()
def release(coin_id: str):
    body = request.get_json() or {}
    force = bool(body.get("force"))
    try:
        return jsonify(quiet_service.release_from_bag(get_jwt_identity(), coin_id, force=force))
    except ValueError as e:
        code = 400
        msg = str(e)
        if msg == "not_in_bag":
            code = 404
        return jsonify({"error": msg}), code


@bp.get("/cooling-bag/<coin_id>")
@jwt_required()
def get_item(coin_id: str):
    item = quiet_service.get_bag_item(get_jwt_identity(), coin_id)
    if not item:
        return jsonify({"in_bag": False, "cooling": False})
    return jsonify({"in_bag": True, "cooling": not item["ready"], **item})
