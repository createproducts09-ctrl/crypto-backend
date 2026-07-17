from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import alert_service

bp = Blueprint("alerts", __name__)


@bp.get("")
@jwt_required()
def list_alerts():
    return jsonify({"items": alert_service.list_alerts(get_jwt_identity())})


@bp.post("")
@jwt_required()
def create_alert():
    data = request.get_json() or {}
    if not data.get("coin_id"):
        return jsonify({"error": "coin_id required"}), 400
    return jsonify(alert_service.create_alert(get_jwt_identity(), data)), 201


@bp.delete("/<alert_id>")
@jwt_required()
def delete_alert(alert_id: str):
    ok = alert_service.delete_alert(get_jwt_identity(), alert_id)
    return jsonify({"ok": ok})
