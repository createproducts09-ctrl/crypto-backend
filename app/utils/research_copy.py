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


_OUTLINE_MARK = re.compile(
    r"(?:(?<=^)|(?<=\s))((?:\d+\.\d+)|(?:\d+\.)|(?:[a-d]\.))(?=\s+\S)",
    re.I,
)


def _looks_like_outline(text: str) -> bool:
    dotted = len(re.findall(r"\b\d+\.\d+\b", text))
    numbered = len(re.findall(r"\b\d+\.\s+[A-Za-z]", text))
    return dotted >= 2 or numbered >= 3


def _polish_bullet(bit: str) -> str:
    bit = bit.strip(" •-\t")
    # Drop leading outline markers left on the fragment
    bit = re.sub(r"^(?:\d+\.\d+|\d+\.|[a-d]\.)\s*", "", bit, flags=re.I)
    bit = re.sub(r"\s+", " ", bit).strip()
    if not bit:
        return ""
    # Drop orphan fragments like "a." or "regarding:"
    if len(bit) < 18:
        return ""
    if bit.endswith((":", ";", ",")) and len(bit) < 48:
        return ""
    # Capitalize first letter
    if bit[0].islower():
        bit = bit[0].upper() + bit[1:]
    # Soft trim very long lines
    if len(bit) > 240:
        bit = bit[:237].rstrip() + "…"
    return bit


def split_outline_bullets(text: str, limit: int = 6) -> list[str]:
    """Split CoinGecko-style '1. … 1.1 … 1.2 … a. …' dumps into clean points."""
    prose = clean_prose(text)
    if not prose:
        return []

    # Insert breaks before outline markers so we can split cleanly
    marked = _OUTLINE_MARK.sub(r"\n\1 ", prose)
    raw_parts = [p.strip() for p in marked.split("\n") if p.strip()]

    # Also break long parts that glued a second sentence without a marker
    expanded: list[str] = []
    for part in raw_parts:
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", part)
        if len(sentences) > 1 and len(part) > 120:
            expanded.extend(sentences)
        else:
            expanded.append(part)

    out: list[str] = []
    for part in expanded:
        polished = _polish_bullet(part)
        if not polished:
            continue
        low = polished.lower()
        if low.startswith("using the ") and "ecosystem" in low and len(polished) < 90:
            continue
        if low.startswith("use of the token") and len(polished) < 100:
            continue
        out.append(polished)
        if len(out) >= limit:
            break
    return out


def split_sentences(text: str, limit: int = 5, min_len: int = 36) -> list[str]:
    prose = clean_prose(text)
    if not prose:
        return []

    if _looks_like_outline(prose):
        outline = split_outline_bullets(prose, limit=limit)
        if outline:
            return outline

    parts = re.split(r"(?<=[.!?])\s+", prose)
    out: list[str] = []
    buf = ""
    for part in parts:
        bit = part.strip(" •-\t")
        if not bit:
            continue
        # Rejoin tiny fragments that sentence-split mangled ("1.2 Control…")
        if len(bit) < min_len:
            if buf:
                buf = f"{buf} {bit}".strip()
                if len(buf) >= min_len:
                    out.append(_polish_bullet(buf) or buf)
                    buf = ""
                    if len(out) >= limit:
                        break
            else:
                buf = bit
            continue
        if buf:
            bit = f"{buf} {bit}".strip()
            buf = ""
        polished = _polish_bullet(bit)
        if not polished:
            continue
        out.append(polished)
        if len(out) >= limit:
            break
    if buf and len(out) < limit:
        polished = _polish_bullet(buf)
        if polished:
            out.append(polished)
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


def build_fundamentals(
    coin: dict[str, Any], description: str, categories: list[str]
) -> dict[str, Any]:
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
    homepage = (coin.get("homepage") or "").strip()

    prose = clean_prose(description)
    utility_points = (
        split_outline_bullets(prose, limit=5)
        if _looks_like_outline(prose)
        else split_sentences(prose, limit=4)
    )

    # Snapshot stays readable — never dump broken numbered fragments
    if _looks_like_outline(prose) or not utility_points:
        overview = [
            f"{name} ({symbol}) sits in the {niche} lane"
            + (f" — project site: {homepage}." if homepage else "."),
            f"Market rank #{rank or '—'} · mcap {_fmt_compact(mcap)} · 24h {_pct(change_24h)}.",
            (
                "Token utility centers on "
                + "; ".join(p.rstrip(".") for p in utility_points[:2])
                + "."
            )
            if utility_points
            else "Treat every number here as a research clue, not a buy or sell signal.",
        ]
        # Keep overview bullets short and clean
        overview = [o for o in (_polish_bullet(x) or x for x in overview) if o]
    else:
        overview = utility_points[:4]

    use_cases = [
        f"Often discussed in: {', '.join(cats[:4])}."
        if cats
        else f"Category tags are thin — dig into live usage for {name}.",
    ]
    if utility_points:
        use_cases.extend(utility_points[:3])
    else:
        use_cases.extend(
            [
                "Common research angles: settlement / payments, DeFi collateral, staking, governance, or app utility.",
                "Ask: who actually pays fees or locks value here, and why would they keep doing it?",
            ]
        )

    tokenomics = [
        f"Circulating supply: {circ:,.0f}."
        if isinstance(circ, (int, float))
        else "Circulating supply isn’t cleanly reported — verify on-chain.",
        f"Total supply: {total:,.0f}."
        if isinstance(total, (int, float))
        else "Total supply unclear from the feed.",
        f"Max supply cap: {max_s:,.0f}."
        if isinstance(max_s, (int, float))
        else "No hard max supply in the feed — dilution risk needs a manual check.",
        f"FDV sits near {_fmt_compact(fdv)} — compare that to today’s market cap to sense unlock overhang.",
    ]

    momentum = [
        f"24h move: {_pct(change_24h)} · 7d move: {_pct(change_7d)}.",
        f"Sentiment lean: {sentiment}. Risk posture reads {risk}.",
        f"24h volume {_fmt_compact(volume)} vs market cap {_fmt_compact(mcap)} — thicker volume usually means cleaner price discovery.",
    ]

    strengths = [
        "Recognizable brand and deeper books"
        if (rank or 999) <= 40
        else f"Focused {niche.lower()} narrative that can travel when attention rotates",
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


def build_technical_takeaways(
    ta: dict[str, Any], coin: dict[str, Any] | None = None
) -> list[str]:
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
        f"RSI sits at {float(rsi_v):.1f} ({rsi_read})."
        if isinstance(rsi_v, (int, float))
        else "RSI isn’t available for this window.",
        f"MACD signal leans {macd_sig}; EMA note: {ema}.",
    ]
    if isinstance(support, (int, float)) and isinstance(resistance, (int, float)):
        bullets.append(
            f"Map the range: support near ${support:,.4g} and resistance near ${resistance:,.4g}."
        )
    bullets.append(
        f"For {name}, wait for volume to agree before trusting a breakout fantasy."
    )
    return bullets
