from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import duel_service

bp = Blueprint("duels", __name__)


@bp.post("/entries")
@jwt_required()
def create_entry():
    body = request.get_json() or {}
    try:
        result = duel_service.create_entry(
            get_jwt_identity(),
            coin_id=body.get("coin_id") or "",
            side=body.get("side") or "",
            thesis=body.get("thesis") or "",
            horizon=body.get("horizon") or "7d",
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/entries/<entry_id>/cancel")
@jwt_required()
def cancel_entry(entry_id: str):
    try:
        return jsonify(duel_service.cancel_entry(get_jwt_identity(), entry_id))
    except ValueError as e:
        msg = str(e)
        code = 404 if msg == "not_found" else 400
        return jsonify({"error": msg}), code


@bp.get("/mine")
@jwt_required()
def mine():
    return jsonify(duel_service.list_mine(get_jwt_identity()))


@bp.get("/feed")
@jwt_required()
def feed():
    try:
        limit = min(int(request.args.get("limit", 40)), 100)
    except ValueError:
        limit = 40
    return jsonify({"items": duel_service.feed(limit)})


@bp.get("/leaderboard")
@jwt_required()
def leaderboard():
    return jsonify({"items": duel_service.leaderboard()})


@bp.get("/<duel_id>")
@jwt_required()
def get_duel(duel_id: str):
    duel = duel_service.get_duel(duel_id, get_jwt_identity())
    if not duel:
        return jsonify({"error": "Not found"}), 404
    return jsonify(duel)
