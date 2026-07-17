from flask_socketio import emit, join_room, leave_room

from app.extensions import db, socketio


@socketio.on("connect")
def on_connect():
    emit("connected", {"ok": True})


@socketio.on("join_coin")
def on_join_coin(data):
    coin_id = (data or {}).get("coin_id")
    if not coin_id:
        return
    join_room(f"coin:{coin_id}")
    coin = db.coins.find_one({"id": coin_id}, {"current_price": 1})
    price = coin.get("current_price") if coin else None
    emit("price_tick", {"coin_id": coin_id, "price": float(price) if price is not None else None})


@socketio.on("leave_coin")
def on_leave_coin(data):
    coin_id = (data or {}).get("coin_id")
    if coin_id:
        leave_room(f"coin:{coin_id}")


def broadcast_price(coin_id: str, price: float):
    socketio.emit("price_tick", {"coin_id": coin_id, "price": price}, room=f"coin:{coin_id}")
