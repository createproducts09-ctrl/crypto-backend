from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

TrafficLight = Literal["green", "yellow", "red", "gray"]

CATEGORY_KEYS = (
    "fundamentals",
    "on_chain",
    "developer",
    "tokenomics",
    "liquidity",
    "momentum",
    "risk",
)

CATEGORY_WEIGHTS = {
    "fundamentals": 1.1,
    "on_chain": 1.15,
    "developer": 1.1,
    "tokenomics": 1.2,
    "liquidity": 1.0,
    "momentum": 0.95,
    "risk": 1.15,
}


def _f(v: Any, default: float | None = None) -> float | None:
    if v is None:
        return default
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    if n != n:
        return default
    return n


def _clamp(n: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, n))


def _light_from_score(score: float | None, *, invert: bool = False) -> TrafficLight:
    if score is None:
        return "gray"
    s = 100 - score if invert else score
    if s >= 72:
        return "green"
    if s >= 50:
        return "yellow"
    return "red"


# Unique Alphora evidence paths shown in the UI (never raw vendor field names).
SOURCE_PATHS: dict[str, str] = {
    "price": "market/spot.price",
    "market_cap": "market/cap",
    "fdv": "token/fdv",
    "volume_24h": "market/volume.24h",
    "chg_30d": "momentum/chg.30d",
    "chg_7d": "momentum/chg.7d",
    "chg_24h": "momentum/chg.24h",
    "circulating_ratio": "token/float.ratio",
    "unlock_pressure": "token/unlock.pressure",
    "dev_commits_4w": "dev/commits.4w",
    "dev_forks": "dev/forks",
    "dev_unavailable": "dev/activity",
    "social_followers": "social/reach",
    "tvl": "onchain/tvl",
    "activity_proxy": "onchain/activity.proxy",
    "liquidity_score": "market/liquidity.score",
    "risk_level": "risk/profile",
}


def _source_path(sid: str, override: str | None = None) -> str:
    if override:
        return override
    return SOURCE_PATHS.get(sid) or f"signal/{sid.replace('_', '.')}"


def _signal(
    *,
    sid: str,
    category: str,
    label: str,
    value: Any,
    prior: Any = None,
    delta_pct: float | None = None,
    unit: str = "",
    traffic_light: TrafficLight = "gray",
    source: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    path = _source_path(sid, source)
    return {
        "id": sid,
        "category": category,
        "label": label,
        "value": value,
        "prior": prior,
        "delta_pct": delta_pct,
        "unit": unit,
        "traffic_light": traffic_light,
        "source": path,
        "source_path": path,
        "note": note,
    }


def _fmt_usd(n: float | None) -> str:
    if n is None:
        return "—"
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if abs_n >= 1_000:
        return f"${n / 1_000:.1f}K"
    return f"${n:.2f}"


def _fmt_pct(n: float | None) -> str:
    if n is None:
        return "—"
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def build_signals(coin: dict[str, Any], tvl: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Deterministic evidence signals from market + optional DefiLlama TVL."""
    signals: list[dict[str, Any]] = []
    mcap = _f(coin.get("market_cap"))
    fdv = _f(coin.get("fully_diluted_valuation"))
    vol = _f(coin.get("total_volume"))
    price = _f(coin.get("current_price"))
    chg_30d = _f(coin.get("price_change_percentage_30d"))
    chg_7d = _f(coin.get("price_change_percentage_7d"))
    chg_24h = _f(coin.get("price_change_percentage_24h"))
    circ = _f(coin.get("circulating_supply"))
    total = _f(coin.get("total_supply"))
    max_s = _f(coin.get("max_supply"))
    liq_score = _f(coin.get("liquidity_score"))
    dev = coin.get("developer_data") or {}
    community = coin.get("community_data") or {}

    # Market / valuation
    signals.append(
        _signal(
            sid="price",
            category="fundamentals",
            label="Price",
            value=price,
            unit="usd",
            traffic_light="gray",
            note=f"Spot {_fmt_usd(price)}",
        )
    )
    signals.append(
        _signal(
            sid="market_cap",
            category="fundamentals",
            label="Market cap",
            value=mcap,
            unit="usd",
            traffic_light="green" if (mcap or 0) > 1e9 else ("yellow" if (mcap or 0) > 1e8 else "red"),
            note=f"Market cap {_fmt_usd(mcap)}",
        )
    )
    fdv_mcap = (fdv / mcap) if mcap and fdv and mcap > 0 else None
    val_light: TrafficLight = "gray"
    if fdv_mcap is not None:
        if fdv_mcap <= 1.15:
            val_light = "green"
        elif fdv_mcap <= 1.8:
            val_light = "yellow"
        else:
            val_light = "red"
    signals.append(
        _signal(
            sid="fdv",
            category="tokenomics",
            label="FDV",
            value=fdv,
            unit="usd",
            traffic_light=val_light,
            note=(
                f"FDV {_fmt_usd(fdv)}"
                + (f" ({fdv_mcap:.2f}× mcap)" if fdv_mcap is not None else "")
            ),
        )
    )
    signals.append(
        _signal(
            sid="volume_24h",
            category="liquidity",
            label="24h volume",
            value=vol,
            unit="usd",
            traffic_light=(
                "green"
                if mcap and vol and vol / mcap > 0.05
                else ("yellow" if mcap and vol and vol / mcap > 0.015 else "red")
            ),
            note=f"Volume {_fmt_usd(vol)}"
            + (f" ({(vol / mcap * 100):.1f}% of mcap)" if mcap and vol else ""),
        )
    )

    # Momentum
    for sid, label, chg, cat in (
        ("chg_30d", "30D change", chg_30d, "momentum"),
        ("chg_7d", "7D change", chg_7d, "momentum"),
        ("chg_24h", "24h change", chg_24h, "momentum"),
    ):
        light: TrafficLight = "gray"
        if chg is not None:
            if chg >= 8:
                light = "green"
            elif chg >= -5:
                light = "yellow"
            else:
                light = "red"
        signals.append(
            _signal(
                sid=sid,
                category=cat,
                label=label,
                value=chg,
                delta_pct=chg,
                unit="pct",
                traffic_light=light,
                note=f"{label} {_fmt_pct(chg)}",
            )
        )

    # Tokenomics — float / unlock pressure proxy
    float_ratio = None
    if circ is not None and max_s and max_s > 0:
        float_ratio = circ / max_s
    elif circ is not None and total and total > 0:
        float_ratio = circ / total
    unlock_pressure = None
    if float_ratio is not None:
        unlock_pressure = max(0.0, 1.0 - float_ratio)
    tok_light: TrafficLight = "gray"
    if float_ratio is not None:
        if float_ratio >= 0.75:
            tok_light = "green"
        elif float_ratio >= 0.45:
            tok_light = "yellow"
        else:
            tok_light = "red"
    signals.append(
        _signal(
            sid="circulating_ratio",
            category="tokenomics",
            label="Circulating / max",
            value=round(float_ratio * 100, 1) if float_ratio is not None else None,
            unit="pct",
            traffic_light=tok_light,
            note=(
                f"{float_ratio * 100:.0f}% of supply circulating"
                if float_ratio is not None
                else "Supply data incomplete"
            ),
        )
    )
    if unlock_pressure is not None and unlock_pressure > 0.2:
        remaining_usd = (fdv or mcap or 0) * unlock_pressure if (fdv or mcap) else None
        signals.append(
            _signal(
                sid="unlock_pressure",
                category="tokenomics",
                label="Unlock / dilution pressure",
                value=round(unlock_pressure * 100, 1),
                unit="pct",
                traffic_light="red" if unlock_pressure > 0.4 else "yellow",
                note=(
                    f"~{_fmt_usd(remaining_usd)} still outside circulating supply"
                    if remaining_usd
                    else f"{unlock_pressure * 100:.0f}% of supply not circulating"
                ),
            )
        )

    # Developer
    commits = _f(dev.get("commit_count_4_weeks"))
    stars = _f(dev.get("stars"))
    prs = _f(dev.get("pull_requests_merged"))
    forks = _f(dev.get("forks"))
    if any(x is not None for x in (commits, stars, prs)):
        dev_light: TrafficLight = "gray"
        if (commits or 0) >= 40 or (stars or 0) >= 5000:
            dev_light = "green"
        elif (commits or 0) >= 10 or (stars or 0) >= 500:
            dev_light = "yellow"
        elif commits is not None or stars is not None:
            dev_light = "red"
        signals.append(
            _signal(
                sid="dev_commits_4w",
                category="developer",
                label="Commits (4 weeks)",
                value=commits,
                unit="count",
                traffic_light=dev_light,
                note=f"{int(commits or 0)} commits in 4 weeks; {int(stars or 0)} stars; {int(prs or 0)} PRs merged",
            )
        )
        if forks is not None:
            signals.append(
                _signal(
                    sid="dev_forks",
                    category="developer",
                    label="Repo forks",
                    value=forks,
                    unit="count",
                    traffic_light=dev_light,
                    note=f"{int(forks)} forks",
                )
            )
    else:
        signals.append(
            _signal(
                sid="dev_unavailable",
                category="developer",
                label="Developer activity",
                value=None,
                traffic_light="gray",
                note="Developer metrics unavailable for this asset",
            )
        )

    # Social / community as soft on-chain / social proxy
    twitter = _f(community.get("twitter_followers"))
    reddit = _f(community.get("reddit_subscribers"))
    if twitter or reddit:
        social_light: TrafficLight = (
            "green" if (twitter or 0) > 500_000 else ("yellow" if (twitter or 0) > 50_000 else "red")
        )
        signals.append(
            _signal(
                sid="social_followers",
                category="on_chain",
                label="Social reach",
                value=twitter or reddit,
                unit="count",
                traffic_light=social_light,
                note=f"X followers {int(twitter or 0):,} · Reddit {int(reddit or 0):,}",
            )
        )

    # On-chain / TVL
    if tvl and tvl.get("tvl") is not None:
        tvl_val = _f(tvl.get("tvl"))
        chg7 = _f(tvl.get("change_7d"))
        tvl_light: TrafficLight = "gray"
        if chg7 is not None:
            if chg7 >= 8:
                tvl_light = "green"
            elif chg7 >= -5:
                tvl_light = "yellow"
            else:
                tvl_light = "red"
        elif tvl_val and tvl_val > 100_000_000:
            tvl_light = "green"
        elif tvl_val and tvl_val > 10_000_000:
            tvl_light = "yellow"
        signals.append(
            _signal(
                sid="tvl",
                category="on_chain",
                label="TVL",
                value=tvl_val,
                delta_pct=chg7,
                unit="usd",
                traffic_light=tvl_light,
                note=f"TVL {_fmt_usd(tvl_val)}"
                + (f" · 7D {_fmt_pct(chg7)}" if chg7 is not None else ""),
            )
        )
    else:
        # Volume/mcap as activity proxy when TVL missing
        activity = (vol / mcap) if mcap and vol and mcap > 0 else None
        signals.append(
            _signal(
                sid="activity_proxy",
                category="on_chain",
                label="Activity proxy (vol/mcap)",
                value=round(activity * 100, 2) if activity is not None else None,
                unit="pct",
                traffic_light=(
                    "green"
                    if activity and activity > 0.05
                    else ("yellow" if activity and activity > 0.015 else ("red" if activity is not None else "gray"))
                ),
                note=(
                    f"Turnover {activity * 100:.2f}% of mcap (TVL unavailable)"
                    if activity is not None
                    else "On-chain TVL unavailable"
                ),
            )
        )

    # Liquidity score
    signals.append(
        _signal(
            sid="liquidity_score",
            category="liquidity",
            label="Liquidity score",
            value=liq_score,
            unit="score",
            traffic_light=_light_from_score(liq_score),
            note=f"Liquidity score {liq_score if liq_score is not None else '—'}/100",
        )
    )

    # Risk from existing heuristic
    risk = coin.get("risk") or {}
    level = (risk.get("level") or "medium").lower()
    risk_score = {"low": 82.0, "medium": 62.0, "high": 38.0}.get(level, 55.0)
    signals.append(
        _signal(
            sid="risk_level",
            category="risk",
            label="Risk level",
            value=risk_score,
            unit="score",
            traffic_light={"low": "green", "medium": "yellow", "high": "red"}.get(level, "gray"),  # type: ignore[arg-type]
            note=f"Risk {level} (confidence {risk.get('confidence', '—')})",
        )
    )

    return signals


def score_categories(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate signals into category scores with evidence ids."""
    by_cat: dict[str, list[dict]] = {k: [] for k in CATEGORY_KEYS}
    for s in signals:
        cat = s.get("category")
        if cat in by_cat:
            by_cat[cat].append(s)

    out: dict[str, dict[str, Any]] = {}
    for cat in CATEGORY_KEYS:
        items = by_cat[cat]
        numeric: list[float] = []
        evidence_ids: list[str] = []
        for s in items:
            evidence_ids.append(s["id"])
            light = s.get("traffic_light")
            if light == "green":
                numeric.append(84.0)
            elif light == "yellow":
                numeric.append(62.0)
            elif light == "red":
                numeric.append(38.0)
            # gray ignored for average
            # Also blend explicit score-like values
            if s.get("unit") == "score" and s.get("value") is not None:
                numeric.append(float(s["value"]))
            if cat == "momentum" and s.get("delta_pct") is not None:
                # Map pct change into 0-100 softly
                numeric.append(_clamp(50 + float(s["delta_pct"]) * 1.2))
            if cat == "tokenomics" and s["id"] == "circulating_ratio" and s.get("value") is not None:
                numeric.append(_clamp(float(s["value"])))
            if cat == "on_chain" and s["id"] == "tvl" and s.get("delta_pct") is not None:
                numeric.append(_clamp(55 + float(s["delta_pct"]) * 1.5))

        if not numeric:
            score = None
            light: TrafficLight = "gray"
        else:
            score = round(sum(numeric) / len(numeric), 1)
            light = _light_from_score(score)
        out[cat] = {
            "score": score,
            "traffic_light": light,
            "evidence_ids": evidence_ids,
            "label": cat.replace("_", " ").title(),
        }
    return out


def composite_score(categories: dict[str, dict[str, Any]]) -> float | None:
    weighted = 0.0
    total_w = 0.0
    for cat, meta in categories.items():
        s = meta.get("score")
        if s is None:
            continue
        w = CATEGORY_WEIGHTS.get(cat, 1.0)
        weighted += float(s) * w
        total_w += w
    if total_w <= 0:
        return None
    return round(weighted / total_w, 1)


def discover_traffic_lights(categories: dict[str, dict[str, Any]]) -> dict[str, TrafficLight]:
    """Card-facing lights: on-chain, developer, tokenomics, valuation."""
    val = categories.get("tokenomics", {}).get("traffic_light", "gray")
    # Valuation leans on FDV signal which lives in tokenomics; also blend fundamentals
    fund = categories.get("fundamentals", {}).get("traffic_light", "gray")
    if val == "gray":
        val = fund
    return {
        "on_chain": categories.get("on_chain", {}).get("traffic_light", "gray"),
        "developer": categories.get("developer", {}).get("traffic_light", "gray"),
        "tokenomics": categories.get("tokenomics", {}).get("traffic_light", "gray"),
        "valuation": val,
    }


def why_interesting(coin: dict[str, Any], signals: list[dict[str, Any]], score: float | None) -> str:
    name = coin.get("symbol") or coin.get("name") or "This asset"
    tvl = next((s for s in signals if s["id"] == "tvl"), None)
    mom = next((s for s in signals if s["id"] == "chg_30d"), None)
    dev = next((s for s in signals if s["id"] == "dev_commits_4w"), None)
    parts: list[str] = []
    if tvl and tvl.get("delta_pct") is not None and tvl["delta_pct"] > 0:
        parts.append(f"TVL {_fmt_pct(tvl['delta_pct'])} over 7D while market still re-rates.")
    elif mom and (mom.get("value") or 0) > 5:
        parts.append(f"Price up {_fmt_pct(mom.get('value'))} over 30D with constructive tape.")
    if dev and (dev.get("value") or 0) >= 20:
        parts.append(f"Developer cadence looks active ({int(dev['value'])} commits / 4 weeks).")
    if not parts:
        rank = coin.get("market_cap_rank")
        parts.append(
            f"{name} screens at research score {score if score is not None else '—'}"
            + (f" (rank #{rank})" if rank else "")
            + "."
        )
    return " ".join(parts[:2])


def biggest_concern(coin: dict[str, Any], signals: list[dict[str, Any]]) -> str:
    unlock = next((s for s in signals if s["id"] == "unlock_pressure"), None)
    if unlock and unlock.get("note"):
        return unlock["note"]
    fdv = next((s for s in signals if s["id"] == "fdv"), None)
    if fdv and fdv.get("traffic_light") == "red":
        return fdv.get("note") or "FDV looks elevated versus circulating market cap."
    risk = next((s for s in signals if s["id"] == "risk_level"), None)
    if risk and risk.get("traffic_light") == "red":
        return risk.get("note") or "Risk profile is elevated versus large-cap peers."
    liq = next((s for s in signals if s["id"] == "volume_24h"), None)
    if liq and liq.get("traffic_light") == "red":
        return "Liquidity looks thin relative to market cap — exits may be costly."
    return "No single red-flag metric; monitor unlocks, liquidity, and activity next."


def build_research_pack(coin: dict[str, Any], tvl: dict[str, Any] | None = None) -> dict[str, Any]:
    signals = build_signals(coin, tvl=tvl)
    categories = score_categories(signals)
    score = composite_score(categories)
    lights = discover_traffic_lights(categories)
    pack = {
        "coin_id": coin.get("id"),
        "symbol": coin.get("symbol"),
        "name": coin.get("name"),
        "research_score": score,
        "categories": categories,
        "traffic_lights": lights,
        "signals": signals,
        "why_interesting": why_interesting(coin, signals, score),
        "biggest_concern": biggest_concern(coin, signals),
        "score_rationale": _score_rationale(score, categories, signals),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    return pack


def _score_rationale(
    score: float | None,
    categories: dict[str, dict[str, Any]],
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Why the composite landed where it did — evidence-backed."""
    if score is None:
        return [{"text": "Insufficient data to score.", "evidence_ids": []}]
    ranked = sorted(
        ((k, v) for k, v in categories.items() if v.get("score") is not None),
        key=lambda kv: float(kv[1]["score"]),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    out.append(
        {
            "text": f"Composite research score is {score}/100 from weighted category evidence.",
            "evidence_ids": [s["id"] for s in signals[:6]],
        }
    )
    if ranked:
        best_k, best_v = ranked[0]
        out.append(
            {
                "text": f"Strongest pillar: {best_v['label']} at {best_v['score']}/100.",
                "evidence_ids": best_v.get("evidence_ids") or [],
            }
        )
        weak_k, weak_v = ranked[-1]
        if weak_k != best_k:
            out.append(
                {
                    "text": f"Weakest pillar: {weak_v['label']} at {weak_v['score']}/100.",
                    "evidence_ids": weak_v.get("evidence_ids") or [],
                }
            )
    return out


def snapshot_metrics(pack: dict[str, Any], coin: dict[str, Any]) -> dict[str, Any]:
    """Flatten key metrics for change detection."""
    sig_map = {s["id"]: s for s in pack.get("signals") or []}

    def val(sid: str):
        s = sig_map.get(sid) or {}
        return s.get("value")

    cats = pack.get("categories") or {}
    return {
        "research_score": pack.get("research_score"),
        "price": coin.get("current_price"),
        "market_cap": coin.get("market_cap"),
        "fdv": coin.get("fully_diluted_valuation"),
        "volume": coin.get("total_volume"),
        "tvl": val("tvl"),
        "chg_30d": val("chg_30d"),
        "circulating_ratio": val("circulating_ratio"),
        "dev_commits_4w": val("dev_commits_4w"),
        "categories": {k: (v or {}).get("score") for k, v in cats.items()},
        "traffic_lights": pack.get("traffic_lights"),
    }
