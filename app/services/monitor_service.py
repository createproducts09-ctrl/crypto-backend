from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.extensions import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def mark_viewed(user_id: str, coin_id: str) -> dict[str, Any]:
    """Record that the user opened research for this coin — baseline for 'since last checked'."""
    now = _now()
    prev = db.research_views.find_one({"user_id": user_id, "coin_id": coin_id})
    prev_at = prev.get("viewed_at") if prev else None
    # Capture snapshot of current metrics as last_seen
    snap = db.coin_snapshots.find_one({"coin_id": coin_id}, sort=[("day_key", -1)])
    metrics = (snap or {}).get("metrics") or {}
    coin = db.coins.find_one({"id": coin_id}, {"_id": 0, "research": 1, "research_score": 1, "current_price": 1})
    if coin and not metrics:
        metrics = {
            "research_score": coin.get("research_score"),
            "price": coin.get("current_price"),
            "categories": ((coin.get("research") or {}).get("categories") or {}),
        }
    db.research_views.update_one(
        {"user_id": user_id, "coin_id": coin_id},
        {
            "$set": {
                "user_id": user_id,
                "coin_id": coin_id,
                "viewed_at": now,
                "last_metrics": metrics,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return {
        "coin_id": coin_id,
        "viewed_at": now.isoformat(),
        "previous_viewed_at": prev_at.isoformat() if isinstance(prev_at, datetime) else None,
    }


def _delta_item(
    key: str,
    label: str,
    prior: Any,
    current: Any,
    *,
    unit: str = "",
    higher_is_good: bool = True,
) -> dict[str, Any] | None:
    if prior is None or current is None:
        return None
    try:
        p = float(prior)
        c = float(current)
    except (TypeError, ValueError):
        return None
    if p == 0:
        delta_pct = None
        abs_delta = c - p
    else:
        abs_delta = c - p
        delta_pct = (c - p) / abs(p) * 100.0
    if abs(abs_delta) < 1e-9 and (delta_pct is None or abs(delta_pct) < 0.25):
        return None
    direction = "positive" if (abs_delta > 0) == higher_is_good else "negative"
    if abs(abs_delta) < 1e-9:
        direction = "neutral"
    return {
        "key": key,
        "label": label,
        "prior": p,
        "current": c,
        "delta": abs_delta,
        "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
        "unit": unit,
        "direction": direction,
    }


def what_changed(
    coin_id: str,
    user_id: str | None = None,
    since_days: int = 7,
) -> dict[str, Any]:
    """Diff current metrics vs user's last view, or vs snapshot N days ago."""
    current_snap = db.coin_snapshots.find_one({"coin_id": coin_id}, sort=[("day_key", -1)])
    current = (current_snap or {}).get("metrics") or {}
    coin = db.coins.find_one(
        {"id": coin_id},
        {"_id": 0, "research_score": 1, "current_price": 1, "market_cap": 1, "research": 1, "symbol": 1, "name": 1},
    )
    if coin:
        current = {
            **current,
            "research_score": coin.get("research_score") if coin.get("research_score") is not None else current.get("research_score"),
            "price": coin.get("current_price") if coin.get("current_price") is not None else current.get("price"),
            "market_cap": coin.get("market_cap") if coin.get("market_cap") is not None else current.get("market_cap"),
        }

    baseline: dict[str, Any] = {}
    baseline_at = None
    mode = "snapshot"

    if user_id:
        view = db.research_views.find_one({"user_id": user_id, "coin_id": coin_id})
        if view and view.get("last_metrics"):
            baseline = view.get("last_metrics") or {}
            baseline_at = _as_utc(view.get("viewed_at"))
            mode = "since_last_check"

    if not baseline:
        day = (_now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        old = db.coin_snapshots.find_one(
            {"coin_id": coin_id, "day_key": {"$lte": day}},
            sort=[("day_key", -1)],
        )
        if not old:
            # earliest snapshot older than latest
            snaps = list(
                db.coin_snapshots.find({"coin_id": coin_id}).sort("day_key", 1).limit(2)
            )
            if len(snaps) >= 2:
                old = snaps[0]
        if old:
            baseline = old.get("metrics") or {}
            baseline_at = _as_utc(old.get("updated_at") or old.get("created_at"))
            mode = f"since_{since_days}d"

    positive: list[dict] = []
    negative: list[dict] = []
    new_items: list[dict] = []

    checks = [
        ("research_score", "Research score", "score", True),
        ("price", "Price", "usd", True),
        ("tvl", "TVL", "usd", True),
        ("volume", "Volume", "usd", True),
        ("dev_commits_4w", "Developer activity", "count", True),
        ("circulating_ratio", "Circulating supply ratio", "pct", True),
        ("chg_30d", "30D momentum", "pct", True),
    ]
    for key, label, unit, good in checks:
        item = _delta_item(
            key,
            label,
            baseline.get(key),
            current.get(key),
            unit=unit,
            higher_is_good=good,
        )
        if not item:
            continue
        if item["direction"] == "positive":
            positive.append(item)
        elif item["direction"] == "negative":
            negative.append(item)

    # Category deltas
    base_cats = baseline.get("categories") or {}
    cur_cats = current.get("categories") or {}
    if isinstance(base_cats, dict) and isinstance(cur_cats, dict):
        for cat, cur_score in cur_cats.items():
            prior_score = base_cats.get(cat)
            if isinstance(prior_score, dict):
                prior_score = prior_score.get("score")
            if isinstance(cur_score, dict):
                cur_score = cur_score.get("score")
            item = _delta_item(f"cat_{cat}", cat.replace("_", " ").title(), prior_score, cur_score, unit="score", higher_is_good=True)
            if not item:
                continue
            if abs(item.get("delta") or 0) < 2:
                continue
            if item["direction"] == "positive":
                positive.append(item)
            else:
                negative.append(item)

    # Unlock / concern as "new" if score dropped on tokenomics
    concern = ((coin or {}).get("research") or {}).get("biggest_concern")
    tok_now = cur_cats.get("tokenomics")
    tok_was = base_cats.get("tokenomics")
    if isinstance(tok_now, dict):
        tok_now = tok_now.get("score")
    if isinstance(tok_was, dict):
        tok_was = tok_was.get("score")
    try:
        if tok_now is not None and tok_was is not None and float(tok_now) + 3 < float(tok_was) and concern:
            new_items.append({"label": "Tokenomics weakened", "detail": concern, "direction": "negative"})
    except (TypeError, ValueError):
        pass

    score_now = current.get("research_score")
    score_was = baseline.get("research_score")
    thesis_note = "Not enough history to judge thesis drift yet."
    try:
        if score_now is not None and score_was is not None:
            diff = float(score_now) - float(score_was)
            if diff >= 3:
                thesis_note = "The thesis has strengthened slightly since your last review."
            elif diff <= -3:
                thesis_note = "The thesis has weakened since your last review."
            else:
                thesis_note = "The thesis is largely unchanged since your last review."
    except (TypeError, ValueError):
        pass

    summary_bits = []
    if positive:
        summary_bits.append(f"{len(positive)} positive")
    if negative:
        summary_bits.append(f"{len(negative)} negative")
    summary = ", ".join(summary_bits) + " moves" if summary_bits else "No material changes detected"

    return {
        "coin_id": coin_id,
        "symbol": (coin or {}).get("symbol"),
        "name": (coin or {}).get("name"),
        "mode": mode,
        "baseline_at": baseline_at.isoformat() if baseline_at else None,
        "positive": positive[:8],
        "negative": negative[:8],
        "new": new_items[:6],
        "summary": summary,
        "thesis_note": thesis_note,
        "current_score": score_now,
        "prior_score": score_was,
    }


def watchlist_changes(user_id: str) -> dict[str, Any]:
    """Intelligent watchlist: things that changed across watched names."""
    items = list(db.watchlist.find({"user_id": user_id}).sort("created_at", -1))
    changes: list[dict] = []
    for item in items:
        cid = item["coin_id"]
        delta = what_changed(cid, user_id=user_id, since_days=7)
        severity = "neutral"
        headline = None
        if delta.get("negative"):
            top = delta["negative"][0]
            severity = "negative"
            pct = top.get("delta_pct")
            headline = f"{top['label']} {pct:+.1f}%" if pct is not None else top["label"]
        elif delta.get("positive"):
            top = delta["positive"][0]
            severity = "positive"
            pct = top.get("delta_pct")
            headline = f"{top['label']} {pct:+.1f}%" if pct is not None else top["label"]
        elif delta.get("new"):
            severity = "new"
            headline = delta["new"][0].get("label")
        if not headline:
            continue
        coin = db.coins.find_one(
            {"id": cid},
            {"_id": 0, "id": 1, "name": 1, "symbol": 1, "image": 1, "research_score": 1, "current_price": 1},
        )
        changes.append(
            {
                "coin_id": cid,
                "severity": severity,
                "headline": headline,
                "thesis_note": delta.get("thesis_note"),
                "detail": delta,
                "coin": coin,
            }
        )

    # Sort: negative first, then new, then positive
    order = {"negative": 0, "new": 1, "positive": 2, "neutral": 3}
    changes.sort(key=lambda c: order.get(c["severity"], 9))
    return {
        "changed_count": len(changes),
        "items": changes[:40],
        "summary": f"{len(changes)} thing{'s' if len(changes) != 1 else ''} changed" if changes else "No material watchlist changes",
    }


def thesis_health_for_basket(user_id: str, basket_id: str) -> dict[str, Any] | None:
    """Basket-as-thesis health from constituent research score deltas."""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(basket_id)
    except (InvalidId, TypeError):
        return None
    doc = db.baskets.find_one({"_id": oid, "user_id": user_id})
    if not doc:
        return None

    assets = doc.get("assets") or []
    strengthening = []
    weakening = []
    scores = []
    constituents = []
    for a in assets:
        cid = a.get("coin_id")
        if not cid:
            continue
        coin = db.coins.find_one(
            {"id": cid},
            {"_id": 0, "id": 1, "name": 1, "symbol": 1, "image": 1, "research_score": 1, "research": 1},
        )
        delta = what_changed(cid, user_id=user_id, since_days=7)
        score = (coin or {}).get("research_score")
        if score is not None:
            scores.append(float(score))
        prior = delta.get("prior_score")
        cur = delta.get("current_score")
        status = "stable"
        try:
            if cur is not None and prior is not None:
                d = float(cur) - float(prior)
                if d >= 3:
                    status = "strengthening"
                    strengthening.append(cid)
                elif d <= -3:
                    status = "weakening"
                    weakening.append(cid)
        except (TypeError, ValueError):
            pass
        constituents.append(
            {
                "coin_id": cid,
                "symbol": (coin or {}).get("symbol"),
                "name": (coin or {}).get("name"),
                "image": (coin or {}).get("image"),
                "research_score": score,
                "status": status,
                "thesis_note": delta.get("thesis_note"),
                "biggest_concern": ((coin or {}).get("research") or {}).get("biggest_concern"),
            }
        )

    health = round(sum(scores) / len(scores), 1) if scores else None
    narrative = "Thesis looks stable."
    if weakening and not strengthening:
        w = next((c for c in constituents if c["status"] == "weakening"), None)
        narrative = (
            f"Your thesis has weakened because {w.get('symbol') if w else 'an asset'} "
            f"deteriorated — {w.get('biggest_concern') if w else 'see details'}."
        )
    elif strengthening and not weakening:
        narrative = f"Your thesis is strengthening — {len(strengthening)} asset(s) improved."
    elif strengthening and weakening:
        narrative = (
            f"{len(strengthening)} assets strengthening, {len(weakening)} weakening."
        )

    return {
        "basket_id": basket_id,
        "name": doc.get("name") or "Thesis",
        "note": doc.get("note") or "",
        "is_thesis": True,
        "thesis_health": health,
        "strengthening_count": len(strengthening),
        "weakening_count": len(weakening),
        "stable_count": len(constituents) - len(strengthening) - len(weakening),
        "narrative": narrative,
        "constituents": constituents,
    }
