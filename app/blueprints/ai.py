from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import ai_chat_service

bp = Blueprint("ai", __name__)


@bp.get("/threads")
@jwt_required()
def threads():
    return jsonify({"items": ai_chat_service.list_threads(get_jwt_identity())})


@bp.get("/threads/<thread_id>")
@jwt_required()
def thread(thread_id: str):
    data = ai_chat_service.get_thread(get_jwt_identity(), thread_id)
    if not data:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@bp.post("/chat")
@jwt_required()
def chat():
    data = request.get_json() or {}
    if not (data.get("content") or "").strip():
        return jsonify({"error": "content required"}), 400
    try:
        result = ai_chat_service.send_message(get_jwt_identity(), data)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        return jsonify({"error": "AI temporarily unavailable. Please try again in a moment."}), 503
