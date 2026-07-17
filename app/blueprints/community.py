from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request

from app.services import auth_service, community_service

bp = Blueprint("community", __name__)


def _optional_user_id() -> str | None:
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:
        return None


@bp.get("/sections")
def sections():
    return jsonify({"items": community_service.list_sections()})


@bp.get("/posts")
def posts():
    section = request.args.get("section")
    user_id = _optional_user_id()
    return jsonify({"items": community_service.list_posts(section, user_id=user_id)})


@bp.post("/posts")
@jwt_required()
def create_post():
    user = auth_service.get_user(get_jwt_identity())
    data = request.get_json() or {}
    if not data.get("title") or not data.get("body"):
        return jsonify({"error": "title and body required"}), 400
    post = community_service.create_post(get_jwt_identity(), user["username"], data)
    return jsonify(post), 201


@bp.get("/posts/<post_id>")
def get_post(post_id: str):
    user_id = _optional_user_id()
    post = community_service.get_post(post_id, user_id=user_id)
    if not post:
        return jsonify({"error": "Not found"}), 404
    return jsonify(post)


@bp.post("/posts/<post_id>/comments")
@jwt_required()
def comment(post_id: str):
    user = auth_service.get_user(get_jwt_identity())
    data = request.get_json() or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body required"}), 400
    return jsonify(community_service.add_comment(get_jwt_identity(), user["username"], post_id, body)), 201


@bp.post("/posts/<post_id>/vote")
@jwt_required()
def vote(post_id: str):
    data = request.get_json() or {}
    direction = data.get("direction", "up")
    if direction not in {"up", "down"}:
        return jsonify({"error": "invalid direction"}), 400
    post = community_service.vote_post(get_jwt_identity(), post_id, direction)
    if not post:
        return jsonify({"error": "Not found"}), 404
    return jsonify(post)


@bp.post("/posts/<post_id>/bookmark")
@jwt_required()
def bookmark(post_id: str):
    post = community_service.bookmark_post(get_jwt_identity(), post_id)
    if not post:
        return jsonify({"error": "Not found"}), 404
    return jsonify(post)
