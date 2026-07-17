from flask import Blueprint, jsonify, request

from app.services import search_service

bp = Blueprint("search", __name__)


@bp.get("")
def search():
    q = request.args.get("q", "")
    return jsonify(search_service.global_search(q))
