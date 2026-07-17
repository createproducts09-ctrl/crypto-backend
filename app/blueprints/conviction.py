from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import conviction_service

bp = Blueprint("conviction", __name__)


@bp.get("")
@jwt_required()
def list_items():
    status = request.args.get("status")
    return jsonify({"items": conviction_service.list_convictions(get_jwt_identity(), status)})


@bp.get("/summary")
@jwt_required()
def summary():
    return jsonify(conviction_service.get_summary(get_jwt_identity()))


@bp.post("")
@jwt_required()
def create():
    body = request.get_json() or {}
    try:
        item = conviction_service.upsert_conviction(
            get_jwt_identity(),
            coin_id=body.get("coin_id") or "",
            thesis=body.get("thesis") or "",
            side=body.get("side") or "bull",
            source=body.get("source") or "discover",
            entry_price=body.get("entry_price"),
        )
        return jsonify(item), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.patch("/<conviction_id>")
@jwt_required()
def patch(conviction_id: str):
    body = request.get_json() or {}
    item = conviction_service.patch_conviction(get_jwt_identity(), conviction_id, body)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(item)


@bp.delete("/<conviction_id>")
@jwt_required()
def delete(conviction_id: str):
    ok = conviction_service.delete_conviction(get_jwt_identity(), conviction_id)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})
