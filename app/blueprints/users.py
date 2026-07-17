from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import auth_service

bp = Blueprint("users", __name__)


@bp.get("/me")
@jwt_required()
def me():
    user = auth_service.get_user(get_jwt_identity())
    if not user:
        return jsonify({"error": "Not found"}), 404
    return jsonify(user)


@bp.patch("/me")
@jwt_required()
def update_me():
    data = request.get_json() or {}
    user = auth_service.update_user(get_jwt_identity(), data)
    return jsonify(user)


@bp.delete("/me")
@jwt_required()
def delete_me():
    ok, err = auth_service.delete_account(get_jwt_identity())
    if not ok:
        return jsonify({"error": err or "Could not delete account"}), 400
    return jsonify({"ok": True, "deleted": True})
