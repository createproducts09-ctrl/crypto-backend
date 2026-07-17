from flask import Blueprint, jsonify, request

from app.services import news_service

bp = Blueprint("news", __name__)


@bp.get("")
def list_news():
    category = request.args.get("category")
    limit = min(int(request.args.get("limit", 40)), 100)
    return jsonify({"items": news_service.list_news(category, limit)})


@bp.get("/<external_id>")
def article(external_id: str):
    item = news_service.get_article(external_id)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(item)


@bp.post("/sync")
def sync():
    try:
        count = news_service.sync_news_feed()
        return jsonify({"synced": count})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
