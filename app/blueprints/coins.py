from flask import Blueprint, jsonify, request

from app.services import coin_service

bp = Blueprint("coins", __name__)


@bp.get("")
def list_coins():
    limit = min(int(request.args.get("limit", 100)), 250)
    skip = int(request.args.get("skip", 0))
    coins = coin_service.ensure_seed_markets() if skip == 0 else coin_service.list_cached_coins(limit, skip)
    return jsonify({"items": coins[:limit]})


@bp.get("/<coin_id>")
def coin_detail(coin_id: str):
    coin = coin_service.get_coin(coin_id)
    if not coin:
        return jsonify({"error": "Coin not found"}), 404
    return jsonify(coin)


@bp.get("/<coin_id>/chart")
def coin_chart(coin_id: str):
    timeframe = request.args.get("timeframe", "7D")
    try:
        chart = coin_service.get_chart(coin_id, timeframe)
        return jsonify(chart)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
