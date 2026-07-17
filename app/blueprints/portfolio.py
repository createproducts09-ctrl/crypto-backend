from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import portfolio_service

bp = Blueprint("portfolio", __name__)


def _parse_float(value, field: str, required: bool = False, min_value: float | None = 0.0):
    if value is None or value == "":
        if required:
            raise ValueError(f"{field} required")
        return 0.0 if min_value is not None else None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if n != n:
        raise ValueError(f"{field} must be a number")
    if min_value is not None and n < min_value:
        raise ValueError(f"{field} must be >= {min_value}")
    return n


@bp.get("")
@jwt_required()
def get_portfolio():
    return jsonify(portfolio_service.portfolio_summary(get_jwt_identity()))


@bp.get("/baskets")
@jwt_required()
def list_baskets():
    return jsonify({"items": portfolio_service.list_baskets(get_jwt_identity())})


@bp.post("/baskets")
@jwt_required()
def create_basket():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    coin_ids = data.get("coin_ids") or []
    if not isinstance(coin_ids, list):
        return jsonify({"error": "coin_ids must be a list"}), 400
    coin_ids = [str(c).strip() for c in coin_ids if str(c).strip()]
    basket = portfolio_service.create_basket(
        get_jwt_identity(),
        name=name,
        coin_ids=coin_ids,
        # Prefer explicit selection; import only if no coins selected
        import_watchlist=bool(data.get("import_watchlist")) and not coin_ids,
        note=data.get("note") or "",
    )
    return jsonify(basket), 201


@bp.get("/baskets/<basket_id>")
@jwt_required()
def get_basket(basket_id: str):
    basket = portfolio_service.get_basket(get_jwt_identity(), basket_id)
    if not basket:
        return jsonify({"error": "Not found"}), 404
    return jsonify(basket)


@bp.patch("/baskets/<basket_id>")
@jwt_required()
def update_basket(basket_id: str):
    data = request.get_json() or {}
    basket = portfolio_service.update_basket(get_jwt_identity(), basket_id, data)
    if not basket:
        return jsonify({"error": "Not found or invalid name"}), 404
    return jsonify(basket)


@bp.delete("/baskets/<basket_id>")
@jwt_required()
def delete_basket(basket_id: str):
    ok = portfolio_service.delete_basket(get_jwt_identity(), basket_id)
    if not ok:
        return jsonify({"error": "Not found", "ok": False}), 404
    return jsonify({"ok": True})


@bp.post("/baskets/<basket_id>/assets")
@jwt_required()
def add_asset(basket_id: str):
    data = request.get_json() or {}
    coin_id = (data.get("coin_id") or "").strip()
    if not coin_id:
        return jsonify({"error": "coin_id required"}), 400
    basket = portfolio_service.add_asset(get_jwt_identity(), basket_id, coin_id)
    if not basket:
        return jsonify({"error": "Not found"}), 404
    return jsonify(basket)


@bp.delete("/baskets/<basket_id>/assets/<coin_id>")
@jwt_required()
def remove_asset(basket_id: str, coin_id: str):
    basket = portfolio_service.remove_asset(get_jwt_identity(), basket_id, coin_id)
    if not basket:
        return jsonify({"error": "Not found"}), 404
    return jsonify(basket)


@bp.put("/baskets/<basket_id>/assets/<coin_id>")
@jwt_required()
def set_holding(basket_id: str, coin_id: str):
    data = request.get_json() or {}
    try:
        amount = _parse_float(data.get("amount"), "amount", min_value=0.0)
        avg_price = None
        cost_basis = None
        if "avg_price" in data and data.get("avg_price") is not None and data.get("avg_price") != "":
            avg_price = _parse_float(data.get("avg_price"), "avg_price", min_value=0.0)
        if "cost_basis" in data and data.get("cost_basis") is not None and data.get("cost_basis") != "":
            cost_basis = _parse_float(data.get("cost_basis"), "cost_basis", min_value=0.0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        basket = portfolio_service.set_holding(
            get_jwt_identity(),
            basket_id,
            coin_id,
            amount=amount,
            avg_price=avg_price,
            cost_basis=cost_basis,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not basket:
        return jsonify({"error": "Not found"}), 404
    return jsonify(basket)


@bp.post("/holdings")
@jwt_required()
def upsert_holding():
    data = request.get_json() or {}
    coin_id = (data.get("coin_id") or "").strip()
    if not coin_id:
        return jsonify({"error": "coin_id required"}), 400
    try:
        amount = _parse_float(data.get("amount"), "amount", min_value=0.0)
        if data.get("avg_price") is not None and data.get("avg_price") != "":
            avg = _parse_float(data.get("avg_price"), "avg_price", min_value=0.0)
            cost_basis = amount * avg
        else:
            cost_basis = _parse_float(data.get("cost_basis"), "cost_basis", min_value=0.0)
        item = portfolio_service.upsert_holding(get_jwt_identity(), coin_id, amount, cost_basis)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(item), 201


@bp.delete("/holdings/<holding_id>")
@jwt_required()
def delete_holding(holding_id: str):
    ok = portfolio_service.delete_holding(get_jwt_identity(), holding_id)
    if not ok:
        return jsonify({"error": "Not found", "ok": False}), 404
    return jsonify({"ok": True})
