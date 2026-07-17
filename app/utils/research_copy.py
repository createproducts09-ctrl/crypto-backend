from __future__ import annotations

import re
from typing import Any


def clean_prose(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"&nbsp;|&amp;|&quot;|&lt;|&gt;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def split_sentences(text: str, limit: int = 5, min_len: int = 36) -> list[str]:
    prose = clean_prose(text)
    if not prose:
        return []
    parts = re.split(r"(?<=[.!?])\s+", prose)
    out: list[str] = []
    for part in parts:
        bit = part.strip(" •-\t")
        if len(bit) < min_len:
            continue
        if len(bit) > 220:
            bit = bit[:217].rstrip() + "…"
        out.append(bit)
        if len(out) >= limit:
            break
    return out


def _fmt_compact(n: float | int | None) -> str:
    if n is None:
        return "—"
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "—"
    abs_v = abs(v)
    if abs_v >= 1_000_000_000_000:
        return f"${v / 1_000_000_000_000:.2f}T"
    if abs_v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if abs_v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs_v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def _pct(n: float | None) -> str:
    if n is None:
        return "—"
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.2f}%"


def build_fundamentals(coin: dict[str, Any], description: str, categories: list[str]) -> dict[str, Any]:
    """Structured, bullet-first research copy — not a raw dump."""
    name = coin.get("name") or "This asset"
    symbol = coin.get("symbol") or "—"
    rank = coin.get("market_cap_rank")
    change_24h = coin.get("price_change_percentage_24h")
    change_7d = coin.get("price_change_percentage_7d")
    mcap = coin.get("market_cap")
    volume = coin.get("total_volume")
    circ = coin.get("circulating_supply")
    total = coin.get("total_supply")
    max_s = coin.get("max_supply")
    fdv = coin.get("fully_diluted_valuation")
    risk = (coin.get("risk") or {}).get("level") or "medium"
    sentiment = coin.get("sentiment") or "neutral"
    cats = [c for c in (categories or []) if c][:6]
    niche = cats[0] if cats else "digital asset"

    overview = split_sentences(description, limit=4)
    if not overview:
        overview = [
            f"{name} ({symbol}) sits in the {niche.lower()} lane — useful to study for how the narrative and liquidity interact.",
            f"Market rank is #{rank or '—'} with a {_fmt_compact(mcap)} market cap snapshot.",
            "Treat every number here as a research clue, not a buy or sell signal.",
        ]

    use_cases = [
        f"Often discussed in contexts like: {', '.join(cats[:4])}." if cats else f"Category tags are thin — dig into the whitepaper and live usage for {name}.",
        "Common research angles: settlement / payments, DeFi collateral, staking, governance, or app utility.",
        "Ask: who actually pays fees or locks value here, and why would they keep doing it?",
    ]

    tokenomics = [
        f"Circulating supply: {circ:,.0f}." if isinstance(circ, (int, float)) else "Circulating supply isn’t cleanly reported — verify on-chain.",
        f"Total supply: {total:,.0f}." if isinstance(total, (int, float)) else "Total supply unclear from the feed.",
        f"Max supply cap: {max_s:,.0f}." if isinstance(max_s, (int, float)) else "No hard max supply in the feed — dilution risk needs a manual check.",
        f"FDV sits near {_fmt_compact(fdv)} — compare that to today’s market cap to sense unlock overhang.",
    ]

    momentum = [
        f"24h move: {_pct(change_24h)} · 7d move: {_pct(change_7d)}.",
        f"Sentiment lean: {sentiment}. Risk posture reads {risk}.",
        f"24h volume {_fmt_compact(volume)} vs market cap {_fmt_compact(mcap)} — thicker volume usually means cleaner price discovery.",
    ]

    strengths = [
        "Recognizable brand and deeper books" if (rank or 999) <= 40 else f"Focused {niche.lower()} narrative that can travel when attention rotates",
        "Enough public data to compare against peers without guessing in the dark",
        "Liquidity is visible enough to study entries and exits without pure illiquid theater"
        if (volume or 0) > 5_000_000
        else "Still early on liquidity — slips and fakeouts can be louder than the chart",
    ]

    watch_outs = [
        "Narrative whiplash: themes rotate faster than fundamentals catch up",
        "Macro beta: risk-off days can ignore a clean local thesis",
        "Unlock / dilution surprises if max supply and vesting aren’t transparent",
    ]

    catalysts = [
        "Ecosystem expansions, listings, or usage milestones that show up in fees and active addresses",
        "Clearer token utility (staking, burns, governance with real votes) that tightens the story",
        "Relative strength vs category peers when the broader tape is flat",
    ]

    risks = [
        "Volatility can erase weeks of “calm” in a single session",
        "Regulatory tone differs by country — this screen is not legal advice",
        "Smart-contract / protocol / custodian risk depending on how you hold exposure",
        "Thin books amplify both pumps and dumps when volume dries up",
    ]

    how_to_read = [
        "Start with market structure (mcap, volume, supply), then layer narrative and catalysts.",
        "Use Tips mode on labels you don’t know — short explainers beat googling mid-scroll.",
        "Write one sentence thesis before acting: what must stay true for this idea to work?",
    ]

    return {
        "sections": [
            {"key": "snapshot", "title": "Quick snapshot", "icon": "flash", "bullets": overview},
            {"key": "momentum", "title": "Tape & posture", "icon": "pulse", "bullets": momentum},
            {"key": "use_cases", "title": "Where it shows up", "icon": "map", "bullets": use_cases},
            {"key": "tokenomics", "title": "Supply story", "icon": "pie", "bullets": tokenomics},
            {"key": "strengths", "title": "What’s working", "icon": "up", "bullets": strengths},
            {"key": "watch_outs", "title": "Watch-outs", "icon": "alert", "bullets": watch_outs},
            {"key": "catalysts", "title": "Possible catalysts", "icon": "rocket", "bullets": catalysts},
            {"key": "risks", "title": "Risk stack", "icon": "shield", "bullets": risks},
            {"key": "how_to_read", "title": "How to use this page", "icon": "book", "bullets": how_to_read},
        ],
        # Keep flat keys for older clients / search
        "project_overview": overview,
        "use_cases": cats[:5] or use_cases[:2],
        "tokenomics": tokenomics,
        "strengths": strengths,
        "weaknesses": watch_outs,
        "opportunities": catalysts,
        "risks": risks,
        "regulatory": [
            "Rules differ by jurisdiction.",
            "Nothing here is legal, tax, or investment advice.",
        ],
    }


def build_technical_takeaways(ta: dict[str, Any], coin: dict[str, Any] | None = None) -> list[str]:
    name = (coin or {}).get("name") or "This asset"
    trend = str(ta.get("trend") or "sideways")
    rsi_v = ta.get("rsi")
    rsi_read = ta.get("rsi_interpretation") or "neutral"
    macd_sig = ta.get("macd_signal") or "mixed"
    ema = ta.get("ema_crossover") or "no clear cross"
    support = ta.get("support")
    resistance = ta.get("resistance")

    bullets = [
        f"Trend bias reads {trend} — treat it as context, not destiny.",
        f"RSI sits at {float(rsi_v):.1f} ({rsi_read})." if isinstance(rsi_v, (int, float)) else "RSI isn’t available for this window.",
        f"MACD signal leans {macd_sig}; EMA note: {ema}.",
    ]
    if isinstance(support, (int, float)) and isinstance(resistance, (int, float)):
        bullets.append(
            f"Map the range: support near ${support:,.4g} and resistance near ${resistance:,.4g}."
        )
    bullets.append(f"For {name}, wait for volume to agree before trusting a breakout fantasy.")
    return bullets
