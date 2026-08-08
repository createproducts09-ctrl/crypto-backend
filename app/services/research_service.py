from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.clients.ai import ai_service
from app.clients.defillama import defillama
from app.extensions import db
from app.utils.research_score import build_research_pack, snapshot_metrics

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_tvl(coin_id: str) -> dict[str, Any] | None:
    cached = db.coin_tvl.find_one({"coin_id": coin_id})
    if cached:
        at = cached.get("updated_at")
        if isinstance(at, datetime):
            age = (_now() - (at if at.tzinfo else at.replace(tzinfo=timezone.utc))).total_seconds()
            if age < 3600:
                return cached.get("data")
    try:
        data = defillama.tvl_for_coin(coin_id)
    except Exception as exc:
        log.warning("TVL fetch failed for %s: %s", coin_id, exc)
        data = None
    if data:
        db.coin_tvl.update_one(
            {"coin_id": coin_id},
            {"$set": {"coin_id": coin_id, "data": data, "updated_at": _now()}},
            upsert=True,
        )
    return data


def compute_research(coin: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    coin_id = coin.get("id")
    if not coin_id:
        raise ValueError("coin.id required")
    tvl = _fetch_tvl(coin_id)
    pack = build_research_pack(coin, tvl=tvl)
    pack["market"] = {
        "price": coin.get("current_price"),
        "market_cap": coin.get("market_cap"),
        "fdv": coin.get("fully_diluted_valuation"),
        "volume": coin.get("total_volume"),
        "chg_1h": coin.get("price_change_percentage_1h"),
        "chg_24h": coin.get("price_change_percentage_24h"),
        "chg_7d": coin.get("price_change_percentage_7d"),
        "chg_30d": coin.get("price_change_percentage_30d"),
        "circulating_supply": coin.get("circulating_supply"),
        "total_supply": coin.get("total_supply"),
        "max_supply": coin.get("max_supply"),
        "liquidity_score": coin.get("liquidity_score"),
    }
    pack["developer_raw"] = coin.get("developer_data") or {}
    pack["community_raw"] = coin.get("community_data") or {}
    if persist:
        db.coins.update_one(
            {"id": coin_id},
            {
                "$set": {
                    "research": {
                        "research_score": pack["research_score"],
                        "categories": pack["categories"],
                        "traffic_lights": pack["traffic_lights"],
                        "why_interesting": pack["why_interesting"],
                        "biggest_concern": pack["biggest_concern"],
                        "score_rationale": pack["score_rationale"],
                        "computed_at": pack["computed_at"],
                    },
                    "research_score": pack["research_score"],
                }
            },
        )
        # Daily snapshot for monitor loop
        day_key = _now().strftime("%Y-%m-%d")
        metrics = snapshot_metrics(pack, coin)
        db.coin_snapshots.update_one(
            {"coin_id": coin_id, "day_key": day_key},
            {
                "$set": {
                    "coin_id": coin_id,
                    "day_key": day_key,
                    "metrics": metrics,
                    "signals": pack.get("signals") or [],
                    "updated_at": _now(),
                },
                "$setOnInsert": {"created_at": _now()},
            },
            upsert=True,
        )
    return pack


def get_or_compute(coin_id: str, *, force: bool = False) -> dict[str, Any] | None:
    from app.services import coin_service

    coin = coin_service.get_coin(coin_id)
    if not coin:
        return None
    existing = coin.get("research")
    if existing and not force:
        computed = existing.get("computed_at")
        # Reuse up to 30 minutes
        try:
            if computed:
                at = datetime.fromisoformat(str(computed).replace("Z", "+00:00"))
                if (_now() - at).total_seconds() < 1800:
                    tvl = _fetch_tvl(coin_id)
                    pack = build_research_pack(coin, tvl=tvl)
                    # Prefer stored narrative if fresh
                    pack["why_interesting"] = existing.get("why_interesting") or pack["why_interesting"]
                    pack["biggest_concern"] = existing.get("biggest_concern") or pack["biggest_concern"]
                    return pack
        except Exception:
            pass
    return compute_research(coin, persist=True)


def enrich_coin_for_discover(coin: dict[str, Any]) -> dict[str, Any]:
    """Attach lean research card fields. Prefer cache; else score from local fields only (no network)."""
    research = coin.get("research")
    if not research or research.get("research_score") is None:
        try:
            # Fast path for decks: skip DefiLlama / AI — use CoinGecko fields only
            tvl = None
            cached_tvl = db.coin_tvl.find_one({"coin_id": coin.get("id")})
            if cached_tvl:
                tvl = cached_tvl.get("data")
            pack = build_research_pack(coin, tvl=tvl)
            research = {
                "research_score": pack["research_score"],
                "traffic_lights": pack["traffic_lights"],
                "why_interesting": pack["why_interesting"],
                "biggest_concern": pack["biggest_concern"],
                "categories": pack["categories"],
                "score_rationale": pack["score_rationale"],
                "computed_at": pack["computed_at"],
            }
            db.coins.update_one(
                {"id": coin.get("id")},
                {
                    "$set": {
                        "research": research,
                        "research_score": pack["research_score"],
                    }
                },
            )
        except Exception as exc:
            log.warning("Discover enrich failed for %s: %s", coin.get("id"), exc)
            research = None
    if research:
        coin = {**coin}
        coin["research_score"] = research.get("research_score")
        coin["research"] = {
            "research_score": research.get("research_score"),
            "traffic_lights": research.get("traffic_lights"),
            "why_interesting": research.get("why_interesting"),
            "biggest_concern": research.get("biggest_concern"),
            "categories": research.get("categories"),
        }
    return coin


def build_thesis(pack: dict[str, Any], coin: dict[str, Any]) -> dict[str, Any]:
    """Rule-based Bull / Base / Bear + falsifiers (AI can refine later)."""
    name = coin.get("symbol") or coin.get("name") or "Asset"
    score = pack.get("research_score")
    cats = pack.get("categories") or {}
    signals = {s["id"]: s for s in pack.get("signals") or []}
    tvl = signals.get("tvl") or {}
    mom = signals.get("chg_30d") or {}
    unlock = signals.get("unlock_pressure") or {}
    dev = signals.get("dev_commits_4w") or {}

    bull = (
        f"Network and market activity keep improving, "
        f"{name} research score holds above {max(70, int((score or 70)))}, "
        "and ecosystem adoption continues to compound."
    )
    base = (
        f"Growth continues at a moderate pace while valuation stays sensitive to "
        f"{'unlock / dilution pressure' if unlock else 'liquidity and narrative rotation'}."
    )
    bear = (
        f"Token supply pressure or stalled activity pushes {name} into a weaker regime; "
        "momentum fades and risk metrics deteriorate."
    )
    if mom.get("value") is not None and mom["value"] < -10:
        bear = f"Price already soft ({mom.get('note')}); further activity decline would confirm a bear path."
    if tvl.get("delta_pct") is not None and tvl["delta_pct"] > 10:
        bull = f"TVL expanding ({tvl.get('note')}) with supportive market tape — adoption thesis strengthens."

    falsifiers = []
    if tvl.get("value"):
        floor = float(tvl["value"]) * 0.7
        falsifiers.append(
            {
                "metric": "tvl",
                "condition": "falls_below",
                "threshold": floor,
                "label": f"TVL falls below ${floor/1e6:.0f}M",
                "evidence_ids": ["tvl"],
            }
        )
    if mom.get("value") is not None:
        falsifiers.append(
            {
                "metric": "chg_30d",
                "condition": "falls_below",
                "threshold": -25,
                "label": "30D performance worse than -25%",
                "evidence_ids": ["chg_30d"],
            }
        )
    if dev.get("value") is not None:
        falsifiers.append(
            {
                "metric": "dev_commits_4w",
                "condition": "falls_below",
                "threshold": max(5, float(dev["value"]) * 0.4),
                "label": "Developer commits drop sharply for 4+ weeks",
                "evidence_ids": ["dev_commits_4w"],
            }
        )
    if not falsifiers:
        falsifiers.append(
            {
                "metric": "research_score",
                "condition": "falls_below",
                "threshold": 45,
                "label": "Research score falls below 45/100",
                "evidence_ids": [],
            }
        )

    on = (cats.get("on_chain") or {}).get("score")
    tok = (cats.get("tokenomics") or {}).get("score")
    active = "bull" if (score or 0) >= 72 and (on or 0) >= 65 else ("bear" if (score or 0) < 48 or (tok or 0) < 40 else "base")

    return {
        "bull": {"summary": bull, "evidence_ids": [s for s in ("tvl", "chg_30d", "dev_commits_4w") if s in signals]},
        "base": {"summary": base, "evidence_ids": [s for s in ("fdv", "circulating_ratio", "liquidity_score") if s in signals]},
        "bear": {"summary": bear, "evidence_ids": [s for s in ("unlock_pressure", "risk_level", "chg_30d") if s in signals]},
        "active": active,
        "falsifiers": falsifiers,
        "generated_at": _now().isoformat(),
    }


def _fallback_so_what(pack: dict[str, Any], thesis: dict[str, Any]) -> dict[str, Any]:
    score = pack.get("research_score")
    concern = pack.get("biggest_concern") or "Monitor supply and liquidity."
    interesting = pack.get("why_interesting") or "See category scores."
    claims = [
        {
            "text": f"Research score sits at {score}/100.",
            "sentiment": "neutral",
            "evidence_ids": [],
        },
        {
            "text": interesting,
            "sentiment": "positive",
            "evidence_ids": ["tvl", "chg_30d", "dev_commits_4w"],
        },
        {
            "text": f"Biggest concern: {concern}",
            "sentiment": "negative",
            "evidence_ids": ["unlock_pressure", "fdv", "risk_level"],
        },
    ]
    # Filter evidence to existing signals
    sig_ids = {s["id"] for s in pack.get("signals") or []}
    for c in claims:
        c["evidence_ids"] = [e for e in c["evidence_ids"] if e in sig_ids]

    interesting_points = []
    worrying = []
    for s in pack.get("signals") or []:
        if s.get("traffic_light") == "green" and len(interesting_points) < 3:
            interesting_points.append(
                {"title": s["label"], "detail": s.get("note") or "", "evidence_ids": [s["id"]]}
            )
        if s.get("traffic_light") == "red" and len(worrying) < 3:
            worrying.append(
                {"title": s["label"], "detail": s.get("note") or "", "evidence_ids": [s["id"]]}
            )

    headline = (
        f"{pack.get('symbol') or 'Asset'} looks "
        f"{'more interesting' if (score or 0) >= 65 else 'mixed'}, "
        f"but {concern[:80]}"
    )
    return {
        "headline": headline,
        "claims": claims,
        "why_interesting": interesting_points,
        "whats_worrying": worrying,
        "thesis_active": thesis.get("active"),
        "source": "rules",
    }


def generate_so_what(pack: dict[str, Any], coin: dict[str, Any], thesis: dict[str, Any]) -> dict[str, Any]:
    """AI investigator summary grounded in signals — structured JSON with evidence ids."""
    fallback = _fallback_so_what(pack, thesis)
    if not ai_service.enabled:
        return fallback

    lean_signals = [
        {
            "id": s["id"],
            "label": s["label"],
            "value": s.get("value"),
            "delta_pct": s.get("delta_pct"),
            "traffic_light": s.get("traffic_light"),
            "note": s.get("note"),
            "source": s.get("source"),
        }
        for s in (pack.get("signals") or [])[:18]
    ]
    prompt = (
        f"You are Alphora's research investigator. Using ONLY these signals for "
        f"{coin.get('name')} ({coin.get('symbol')}), answer 'So what?'\n"
        f"Research score: {pack.get('research_score')}\n"
        f"Categories: {json.dumps({k: v.get('score') for k, v in (pack.get('categories') or {}).items()})}\n"
        f"Signals JSON:\n{json.dumps(lean_signals)}\n\n"
        "Return STRICT JSON (no markdown) with keys:\n"
        "headline (string, 1 sentence, no buy/sell),\n"
        "claims: [{text, sentiment: positive|negative|neutral, evidence_ids: [signal ids]}],\n"
        "why_interesting: [{title, detail, evidence_ids}],\n"
        "whats_worrying: [{title, detail, evidence_ids}]\n"
        "Rules: every claim MUST cite evidence_ids from the signal list. "
        "Never invent numbers. No buy/sell advice."
    )
    try:
        raw = ai_service._chat([{"role": "user", "content": prompt}])  # noqa: SLF001
        text = (raw or "").strip()
        # Extract JSON object
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return fallback
        data = json.loads(m.group(0))
        sig_ids = {s["id"] for s in pack.get("signals") or []}

        def _clean_items(items: Any) -> list[dict]:
            out = []
            if not isinstance(items, list):
                return out
            for it in items[:5]:
                if not isinstance(it, dict):
                    continue
                eids = [e for e in (it.get("evidence_ids") or []) if e in sig_ids]
                out.append(
                    {
                        "title": str(it.get("title") or it.get("text") or "")[:120],
                        "detail": str(it.get("detail") or it.get("text") or "")[:280],
                        "evidence_ids": eids,
                        "text": str(it.get("text") or it.get("detail") or "")[:280],
                        "sentiment": it.get("sentiment") or "neutral",
                    }
                )
            return out

        claims = []
        for c in data.get("claims") or []:
            if not isinstance(c, dict):
                continue
            eids = [e for e in (c.get("evidence_ids") or []) if e in sig_ids]
            claims.append(
                {
                    "text": str(c.get("text") or "")[:280],
                    "sentiment": c.get("sentiment") or "neutral",
                    "evidence_ids": eids,
                }
            )
        return {
            "headline": str(data.get("headline") or fallback["headline"])[:220],
            "claims": claims or fallback["claims"],
            "why_interesting": _clean_items(data.get("why_interesting")) or fallback["why_interesting"],
            "whats_worrying": _clean_items(data.get("whats_worrying")) or fallback["whats_worrying"],
            "thesis_active": thesis.get("active"),
            "source": "ai",
        }
    except Exception as exc:
        log.warning("so_what AI failed: %s", exc)
        return fallback


def full_research(coin_id: str, *, force: bool = False, with_ai: bool = True) -> dict[str, Any] | None:
    from app.services import coin_service
    from app.services import monitor_service

    coin = coin_service.get_coin(coin_id)
    if not coin:
        return None
    pack = compute_research(coin, persist=True) if force else get_or_compute(coin_id, force=force)
    if not pack:
        return None
    thesis = build_thesis(pack, coin)
    so_what = generate_so_what(pack, coin, thesis) if with_ai else _fallback_so_what(pack, thesis)

    # Persist structured research artifact
    artifact = {
        "coin_id": coin_id,
        "research_score": pack.get("research_score"),
        "categories": pack.get("categories"),
        "traffic_lights": pack.get("traffic_lights"),
        "signals": pack.get("signals"),
        "score_rationale": pack.get("score_rationale"),
        "why_interesting": pack.get("why_interesting"),
        "biggest_concern": pack.get("biggest_concern"),
        "so_what": so_what,
        "thesis": thesis,
        "market": pack.get("market"),
        "updated_at": _now(),
    }
    db.coin_research.update_one(
        {"coin_id": coin_id},
        {"$set": artifact, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    db.coins.update_one(
        {"id": coin_id},
        {
            "$set": {
                "research": {
                    "research_score": artifact["research_score"],
                    "categories": artifact["categories"],
                    "traffic_lights": artifact["traffic_lights"],
                    "why_interesting": artifact["why_interesting"],
                    "biggest_concern": artifact["biggest_concern"],
                    "score_rationale": artifact["score_rationale"],
                    "computed_at": _now().isoformat(),
                },
                "research_score": artifact["research_score"],
                "thesis": thesis,
                "so_what": so_what,
            }
        },
    )
    return {
        **artifact,
        "coin": {
            "id": coin.get("id"),
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "image": coin.get("image"),
            "current_price": coin.get("current_price"),
            "market_cap_rank": coin.get("market_cap_rank"),
            "price_change_percentage_30d": coin.get("price_change_percentage_30d"),
            "sparkline": coin.get("sparkline"),
        },
        "updated_at": artifact["updated_at"].isoformat(),
    }


def compare_coins(coin_ids: list[str]) -> dict[str, Any]:
    rows = []
    for cid in coin_ids[:5]:
        data = full_research(cid, force=False, with_ai=False)
        if not data:
            continue
        cats = data.get("categories") or {}
        rows.append(
            {
                "coin_id": cid,
                "symbol": (data.get("coin") or {}).get("symbol"),
                "name": (data.get("coin") or {}).get("name"),
                "image": (data.get("coin") or {}).get("image"),
                "research_score": data.get("research_score"),
                "fundamentals": (cats.get("fundamentals") or {}).get("score"),
                "on_chain": (cats.get("on_chain") or {}).get("score"),
                "developer": (cats.get("developer") or {}).get("score"),
                "tokenomics": (cats.get("tokenomics") or {}).get("score"),
                "liquidity": (cats.get("liquidity") or {}).get("score"),
                "momentum": (cats.get("momentum") or {}).get("score"),
                "risk": (cats.get("risk") or {}).get("score"),
                "biggest_concern": data.get("biggest_concern"),
                "why_interesting": data.get("why_interesting"),
            }
        )
    conclusion = None
    if len(rows) >= 2:
        a, b = rows[0], rows[1]
        stronger_mom = a if (a.get("momentum") or 0) >= (b.get("momentum") or 0) else b
        stronger_dev = a if (a.get("developer") or 0) >= (b.get("developer") or 0) else b
        conclusion = (
            f"{stronger_mom.get('symbol')} currently has stronger momentum, while "
            f"{stronger_dev.get('symbol')} has stronger developer fundamentals. "
            f"{a.get('symbol')}'s primary weakness: {a.get('biggest_concern')} "
            f"{b.get('symbol')}'s primary weakness: {b.get('biggest_concern')}"
        )
    return {"items": rows, "conclusion": conclusion}


def investigator_answer(question: str, coin_id: str | None = None, coin_ids: list[str] | None = None) -> dict[str, Any]:
    """Grounded investigator over research objects — not a generic chatbot."""
    q = (question or "").strip()
    if not q:
        raise ValueError("question required")

    context_blocks: list[str] = []
    evidence: list[dict] = []

    ids = list(coin_ids or [])
    if coin_id and coin_id not in ids:
        ids.insert(0, coin_id)

    # Compare intent
    if len(ids) >= 2 or re.search(r"\bcompare\b", q, re.I):
        if len(ids) < 2:
            # try extract symbols from question — fallback skip
            pass
        if len(ids) >= 2:
            cmp = compare_coins(ids)
            return {
                "mode": "compare",
                "answer": cmp.get("conclusion") or "Comparison ready.",
                "table": cmp.get("items"),
                "evidence": [],
                "question": q,
            }

    packs = []
    for cid in ids[:3]:
        data = full_research(cid, force=False, with_ai=False)
        if data:
            packs.append(data)
            for s in (data.get("signals") or [])[:12]:
                evidence.append({**s, "coin_id": cid})
            context_blocks.append(
                f"{cid}: score={data.get('research_score')} "
                f"why={data.get('why_interesting')} concern={data.get('biggest_concern')} "
                f"cats={json.dumps({k: (v or {}).get('score') for k, v in (data.get('categories') or {}).items()})}"
            )

    # Score fall question
    if packs and re.search(r"score|fall|drop|change|why", q, re.I):
        from app.services import monitor_service

        cid = packs[0]["coin_id"]
        delta = monitor_service.what_changed(cid, user_id=None, since_days=7)
        answer = (
            f"{packs[0].get('coin', {}).get('symbol') or cid}: "
            f"score {packs[0].get('research_score')}/100. "
            f"{delta.get('summary') or packs[0].get('why_interesting')}"
        )
        return {
            "mode": "investigate",
            "answer": answer,
            "changes": delta,
            "research_score": packs[0].get("research_score"),
            "evidence": evidence[:10],
            "question": q,
        }

    if ai_service.enabled and context_blocks:
        prompt = (
            "You are Alphora investigator. Answer ONLY from the research context. "
            "Cite signal ids in parentheses when using a number. No buy/sell.\n"
            f"Question: {q}\nContext:\n" + "\n".join(context_blocks)
        )
        try:
            answer = ai_service._chat([{"role": "user", "content": prompt}])  # noqa: SLF001
            return {
                "mode": "investigate",
                "answer": answer.strip(),
                "evidence": evidence[:12],
                "question": q,
            }
        except Exception:
            pass

    if packs:
        p = packs[0]
        return {
            "mode": "investigate",
            "answer": (
                f"{(p.get('coin') or {}).get('symbol')}: score {p.get('research_score')}/100. "
                f"{p.get('why_interesting')} Concern: {p.get('biggest_concern')}"
            ),
            "evidence": evidence[:10],
            "question": q,
        }
    return {
        "mode": "investigate",
        "answer": "Attach a coin or run research first — I investigate Alphora data, not generic trivia.",
        "evidence": [],
        "question": q,
    }


def recompute_top_scores(limit: int = 40) -> dict[str, Any]:
    """Background: refresh research scores for top coins."""
    coins = list(db.coins.find({}, {"_id": 0}).sort("market_cap_rank", 1).limit(limit))
    n = 0
    for coin in coins:
        try:
            compute_research(coin, persist=True)
            n += 1
        except Exception as exc:
            log.warning("recompute failed %s: %s", coin.get("id"), exc)
    return {"updated": n}
