from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, verify_jwt_in_request

from app.services import auth_service

bp = Blueprint("auth", __name__)


def _optional_user_id():
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:
        return None


@bp.post("/register")
def register():
    data = request.get_json() or {}
    result, err = auth_service.register_user(
        data.get("email", ""),
        data.get("password", ""),
        data.get("username", ""),
    )
    if err:
        return jsonify({"error": err}), 400
    return jsonify(result), 201


@bp.post("/login")
def login():
    data = request.get_json() or {}
    result, err = auth_service.login_user(data.get("email", ""), data.get("password", ""))
    if err:
        return jsonify({"error": err}), 401
    return jsonify(result)


@bp.post("/verify-email")
def verify_email():
    data = request.get_json() or {}
    user_id = _optional_user_id()
    result, err = auth_service.verify_email(
        user_id=user_id,
        email=data.get("email"),
        code=data.get("code", ""),
    )
    if err:
        return jsonify({"error": err}), 400
    return jsonify(result)


@bp.post("/resend-verification")
def resend_verification():
    data = request.get_json() or {}
    user_id = _optional_user_id()
    result, err = auth_service.resend_verification(
        user_id=user_id,
        email=data.get("email"),
    )
    if err:
        return jsonify({"error": err}), 400
    return jsonify(result)


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    return jsonify({"access_token": create_access_token(identity=identity)})


@bp.get("/me")
@jwt_required()
def me():
    user = auth_service.get_user(get_jwt_identity())
    if not user:
        return jsonify({"error": "Not found"}), 404
    return jsonify(user)
