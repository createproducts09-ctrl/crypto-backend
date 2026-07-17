from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import watchlist_service

bp = Blueprint("watchlist", __name__)


@bp.get("")
@jwt_required()
def list_items():
    return jsonify({"items": watchlist_service.get_watchlist(get_jwt_identity())})


@bp.post("")
@jwt_required()
def add_item():
    data = request.get_json() or {}
    coin_id = data.get("coin_id")
    if not coin_id:
        return jsonify({"error": "coin_id required"}), 400
    item = watchlist_service.add_to_watchlist(
        get_jwt_identity(),
        coin_id,
        category=data.get("category", "default"),
        notes=data.get("notes", ""),
        tags=data.get("tags"),
    )
    return jsonify(item), 201


@bp.patch("/<item_id>")
@jwt_required()
def update_item(item_id: str):
    data = request.get_json() or {}
    item = watchlist_service.update_watchlist_item(get_jwt_identity(), item_id, data)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(item)


@bp.delete("/<coin_id>")
@jwt_required()
def remove_item(coin_id: str):
    ok = watchlist_service.remove_from_watchlist(get_jwt_identity(), coin_id)
    return jsonify({"ok": ok})
