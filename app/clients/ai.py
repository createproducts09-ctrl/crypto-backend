from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Config
from app.utils.ai_text import normalize_model_output

log = logging.getLogger(__name__)


FALLBACK_INSIGHTS = [
    "Momentum is improving with increasing trading volume.",
    "Price action is consolidating near recent support.",
    "Volatility remains elevated; size positions carefully.",
    "Trend strength looks constructive on the medium timeframe.",
    "Liquidity is healthy relative to recent market activity.",
]


class AIRateLimitError(RuntimeError):
    """Groq quota / rate limit exceeded."""


class AIService:
    """Alphora AI — Groq only (openai/gpt-oss-120b)."""

    def __init__(self):
        self._refresh_config()

    def _refresh_config(self) -> None:
        """Reload model settings from .env (avoids stale singleton after edits)."""
        import os

        from dotenv import load_dotenv

        load_dotenv(override=True)

        self.api_key = (
            os.getenv("GROQ_API_KEY")
            or getattr(Config, "GROQ_API_KEY", "")
            or ""
        )
        self.model = (
            os.getenv("GROQ_MODEL")
            or getattr(Config, "GROQ_MODEL", "")
            or "llama-3.3-70b-versatile"
        )
        self.fallback_model = (
            os.getenv("GROQ_FALLBACK_MODEL")
            or getattr(Config, "GROQ_FALLBACK_MODEL", "")
            or "llama-3.1-8b-instant"
        )
        self.temperature = float(
            os.getenv("GROQ_TEMPERATURE")
            or getattr(Config, "GROQ_TEMPERATURE", 1)
            or 1
        )
        self.max_completion_tokens = int(
            os.getenv("GROQ_MAX_COMPLETION_TOKENS")
            or getattr(Config, "GROQ_MAX_COMPLETION_TOKENS", 4096)
            or 4096
        )
        self.top_p = float(
            os.getenv("GROQ_TOP_P") or getattr(Config, "GROQ_TOP_P", 1) or 1
        )
        self.reasoning_effort = (
            os.getenv("GROQ_REASONING_EFFORT")
            or getattr(Config, "GROQ_REASONING_EFFORT", "")
            or "medium"
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def insight_for_coin(self, coin: dict[str, Any]) -> str:
        name = coin.get("name") or coin.get("symbol") or "This asset"
        change = coin.get("price_change_percentage_24h") or 0
        if self.enabled:
            prompt = (
                f"One vivid research-desk sentence about {name} "
                f"({coin.get('symbol', '')}) with 24h change {change:.2f}%. "
                "Calm, specific, zero hype. No buy/sell advice."
            )
            try:
                return self._chat([{"role": "user", "content": prompt}]).strip()
            except Exception:
                pass
        idx = abs(hash(str(coin.get("id", name)))) % len(FALLBACK_INSIGHTS)
        if change > 2:
            return f"{name} is catching a bid today — {FALLBACK_INSIGHTS[idx]}"
        if change < -2:
            return f"{name} is under pressure today — {FALLBACK_INSIGHTS[idx]}"
        return f"{name}: {FALLBACK_INSIGHTS[idx]}"

    def chat(
        self,
        messages: list[dict[str, str]],
        context: str | None = None,
        research_mode: bool = False,
        portfolio_mode: bool = False,
    ) -> str:
        self._refresh_config()
        system = (
            "You are Alphora AI — a crypto research desk assistant from Alphora Labs.\n"
            "Voice: clear, calm, specific. Educational research only — never personalized "
            "buy/sell advice.\n\n"
            "HARD RULES for every reply:\n"
            "1) Answer ONLY the user's latest question. Stay on that ask.\n"
            "2) Never dump raw context, key=value fields, 'Available context', "
            "'Attached coin research', or 'Recent headlines' blocks into the reply.\n"
            "3) Never open with meta filler: 'Here's a structured take', 'You asked', "
            "'Regarding X:', 'Let me clarify', 'As an AI'.\n"
            "4) Use attached market numbers as ground truth. If a figure is missing, say so — "
            "do not invent prices.\n"
            "5) Prefer clean markdown: short headings + bullets. No wall of prose.\n"
            "6) Ignore unrelated news headlines unless the user asked about news/macro.\n"
            "7) When the question is narrow (e.g. narratives, risks, catalysts), answer that "
            "topic only — do not force a full 7-section desk brief."
        )
        if portfolio_mode:
            system += (
                "\n\nWhen the user asks for a FULL portfolio / basket desk brief, respond in "
                "this EXACT markdown structure (same headings, no intro/outro outside sections):\n\n"
                "### 1) Basket Snapshot\n"
                "2–4 short paragraphs on what this basket is trying to do and how it looks today.\n\n"
                "### 2) Holdings Tape\n"
                "Bullet metrics for the basket and top names, e.g.:\n"
                "* **Basket Value**: ...\n"
                "* **Cost Basis**: ...\n"
                "* **P&L**: ...\n"
                "* **Top Weight**: ...\n"
                "Then per-holding bullets for the largest positions.\n\n"
                "### 3) Concentration & Weights\n"
                "Where risk is concentrated (single name, narrative, chain).\n\n"
                "### 4) Performance Read\n"
                "What is driving P&L; winners vs laggards.\n\n"
                "### 5) Narratives Across Names\n"
                "Shared themes / sector beta across the book.\n\n"
                "### 6) Risks & Watch-Outs\n"
                "3–5 concrete basket-level risks.\n\n"
                "### 7) What to Monitor Next\n"
                "Numbered list 1–5 actionable checkpoints for this book.\n\n"
                "Separate sections with ---. Keep tone sharp and professional. "
                "No buy/sell advice. Use only provided holdings context for numbers.\n"
                "If the user asked a focused follow-up (not a full brief), answer that "
                "question only — skip the 7-section template."
            )
        elif research_mode:
            system += (
                "\n\nThe user asked for a FULL coin research desk brief. ALWAYS respond "
                "in this EXACT markdown structure (same headings every time, no extras, "
                "no intro/outro outside sections):\n\n"
                "### 1) Snapshot\n"
                "2–4 short paragraphs. Plain English what it is and why it exists.\n\n"
                "### 2) Market Tape\n"
                "Use bullet metrics exactly like:\n"
                "* **Spot Price**: ...\n"
                "* **Market Capitalization**: ...\n"
                "* **Fully Diluted Valuation (FDV)**: ...\n"
                "* **24-Hour Trading Volume**: ...\n"
                "* **1-Hour**: ...\n"
                "* **24-Hour**: ...\n"
                "* **7-Day**: ...\n"
                "* **30-Day**: ...\n"
                "* **All-Time High (ATH)**: ...\n"
                "* **All-Time Low (ATL)**: ...\n"
                "Then 1–2 short liquidity / desk-metric bullets.\n\n"
                "### 3) Trend & Technical Read\n"
                "Bullets for directional bias, structure, and key levels.\n\n"
                "### 4) Fundamentals\n"
                "Bullets for utility, supply/distribution, ecosystem.\n\n"
                "### 5) Narratives & Catalysts\n"
                "3–5 bullets on why it matters now.\n\n"
                "### 6) Risks & Watch-Outs\n"
                "3–5 concrete risk bullets (not generic).\n\n"
                "### 7) What to Monitor Next\n"
                "Numbered list 1–5 actionable checkpoints.\n\n"
                "Separate sections with ---. Keep tone sharp and professional. "
                "No buy/sell advice. Use only provided market context for numbers."
            )
        if context:
            system += (
                "\n\nINTERNAL FACT SHEET (for your use only — never paste this block):\n"
                f"{context}"
            )

        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        if not self.enabled:
            return self._fallback_reply(
                last,
                context,
                reason="offline",
                research_mode=research_mode,
                portfolio_mode=portfolio_mode,
            )

        full = [{"role": "system", "content": system}, *messages]
        try:
            return self._chat(full)
        except AIRateLimitError as exc:
            # Primary model (often gpt-oss-120b) burns daily TPD fast — retry a lighter model.
            fb = (self.fallback_model or "").strip()
            if fb and fb != self.model:
                log.warning(
                    "Groq rate-limited on %s (%s); retrying with %s",
                    self.model,
                    exc,
                    fb,
                )
                try:
                    return self._chat(full, model_override=fb)
                except Exception as exc2:
                    log.warning("Groq fallback model failed (%s); offline reply", exc2)
            else:
                log.warning("Groq rate-limited (%s); offline reply", exc)
            return self._fallback_reply(
                last,
                context,
                reason="rate_limit",
                research_mode=research_mode,
                portfolio_mode=portfolio_mode,
            )
        except Exception as exc:
            log.warning("Groq error (%s); offline reply", exc)
            return self._fallback_reply(
                last,
                context,
                reason="error",
                research_mode=research_mode,
                portfolio_mode=portfolio_mode,
            )

    def summarize_news(self, title: str, body: str) -> str:
        if not self.enabled:
            text = (body or title or "").strip()
            return (text[:180] + "…") if len(text) > 180 else text
        prompt = (
            f"Summarize this crypto news in 1-2 neutral sentences.\n"
            f"Title: {title}\nBody: {body[:1500]}"
        )
        try:
            return self._chat([{"role": "user", "content": prompt}]).strip()
        except Exception:
            return (body or title)[:180]

    def research_summary(
        self, coin: dict[str, Any], ta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = coin.get("name", "this project")
        symbol = coin.get("symbol") or ""
        change = coin.get("price_change_percentage_24h") or 0
        trend_hint = (
            "bullish" if change > 1 else "bearish" if change < -1 else "sideways"
        )
        ta = ta or {}

        if self.enabled:
            prompt = (
                f"You are writing a crisp research brief for {name} ({symbol}) "
                "inside a mobile crypto app.\n"
                "Tone: sharp, calm, Gen-Z friendly but professional. No hype. "
                "No buy/sell advice.\n"
                "Return EXACTLY this markdown structure (bullets only, 2–3 per section):\n\n"
                "## Should you research?\n"
                "- ...\n"
                "- ...\n\n"
                "## Trend read\n"
                "- ...\n\n"
                "## Risks\n"
                "- ...\n\n"
                "## Opportunities\n"
                "- ...\n\n"
                "## Watch next\n"
                "- ...\n\n"
                f"Market: price={coin.get('current_price')}, 24h={change}, "
                f"mcap={coin.get('market_cap')}, rank={coin.get('market_cap_rank')}. "
                f"TA hint: trend={ta.get('trend')}, rsi={ta.get('rsi')}, "
                f"macd={ta.get('macd_signal')}."
            )
            try:
                text = self._chat([{"role": "user", "content": prompt}])
                parsed = _parse_research_markdown(text)
                if parsed:
                    return parsed
                return {
                    "full": text,
                    "sections": _fallback_research_sections(name, trend_hint, ta),
                }
            except Exception:
                pass

        sections = _fallback_research_sections(name, trend_hint, ta)
        return {
            "should_research": "\n".join(sections[0]["bullets"]),
            "trend": "\n".join(sections[1]["bullets"]),
            "risks": "\n".join(sections[2]["bullets"]),
            "opportunities": "\n".join(sections[3]["bullets"]),
            "monitor_next": "\n".join(sections[4]["bullets"]),
            "sections": sections,
            "full": "",
        }

    def _supports_reasoning_effort(self, model: str | None = None) -> bool:
        # Only gpt-oss family accepts reasoning_effort on Groq today.
        m = (model or self.model or "").lower()
        return "gpt-oss" in m or m.startswith("openai/gpt-oss")

    def _chat(
        self,
        messages: list[dict[str, str]],
        model_override: str | None = None,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")

        model = (model_override or self.model or "").strip() or self.model

        payload_messages: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role") or "user"
            text = (message.get("content") or "").strip()
            if not text:
                continue
            if role not in ("system", "user", "assistant"):
                role = "user"
            payload_messages.append({"role": role, "content": text})

        if not payload_messages:
            raise RuntimeError("Empty Groq chat payload")

        use_reasoning = bool(self.reasoning_effort) and self._supports_reasoning_effort(
            model
        )
        try:
            text = self._groq_complete(
                payload_messages, use_reasoning=use_reasoning, model=model
            )
        except RuntimeError as exc:
            msg = str(exc).lower()
            if use_reasoning and "reasoning_effort" in msg:
                log.warning(
                    "Groq rejected reasoning_effort for %s; retrying without it",
                    model,
                )
                text = self._groq_complete(
                    payload_messages, use_reasoning=False, model=model
                )
            else:
                raise

        cleaned = normalize_model_output(text)
        if not cleaned:
            raise RuntimeError("Groq returned empty text after normalize")

        log.info("AI reply served via Groq model %s", model)
        return cleaned

    def _groq_complete(
        self,
        payload_messages: list[dict[str, str]],
        *,
        use_reasoning: bool,
        model: str | None = None,
    ) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        model_name = (model or self.model or "").strip() or self.model
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": payload_messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
            "top_p": self.top_p,
            "stream": True,
        }
        if use_reasoning and self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        chunks: list[str] = []
        with httpx.Client(timeout=120.0) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 429:
                    detail = _read_error_body(resp)
                    raise AIRateLimitError(detail or "Groq rate limit")
                if resp.status_code >= 400:
                    detail = _read_error_body(resp)
                    raise RuntimeError(
                        f"Groq HTTP {resp.status_code}: {detail or resp.reason_phrase}"
                    )

                for line in resp.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="ignore")
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if not piece and isinstance(delta.get("message"), dict):
                        piece = delta["message"].get("content") or ""
                    if piece:
                        chunks.append(piece)

        text = "".join(chunks).strip()
        if text:
            return text

        # Empty stream body — retry once without streaming.
        payload_ns = {**payload, "stream": False}
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, headers=headers, json=payload_ns)
        if resp.status_code == 429:
            detail = ""
            try:
                detail = (resp.json().get("error") or {}).get("message") or ""
            except Exception:
                detail = (resp.text or "")[:200]
            raise AIRateLimitError(detail or "Groq rate limit")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = (resp.json().get("error") or {}).get("message") or ""
            except Exception:
                detail = (resp.text or "")[:300]
            raise RuntimeError(
                f"Groq HTTP {resp.status_code}: {detail or resp.reason_phrase}"
            )
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Groq returned no choices")
        msg = choices[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            raise RuntimeError("Groq returned empty text")
        return text

    def _fallback_reply(
        self,
        question: str,
        context: str | None,
        reason: str = "offline",
        research_mode: bool = False,
        portfolio_mode: bool = False,
    ) -> str:
        """Local research reply when Groq is unavailable — never dump raw context."""
        q = (question or "").strip()
        q_low = q.lower()
        _ = reason
        ctx = context or ""

        # Pull light facts from internal context without exposing the dump.
        name = _ctx_field(ctx, "name") or "This asset"
        symbol = (_ctx_field(ctx, "symbol") or "").upper()
        categories = _ctx_field(ctx, "categories") or ""
        about = _ctx_field(ctx, "about") or ""
        chg24 = _ctx_field(ctx, "change_24h")
        rank = _ctx_field(ctx, "rank")
        label = f"{name} ({symbol})" if symbol else name

        # Full desk briefs must win over keyword stubs — the brief template itself
        # contains words like "Narratives & Catalysts" / "Risks".
        if research_mode or portfolio_mode or _is_full_brief_request(q_low):
            return normalize_model_output(
                _fallback_full_desk_brief(
                    ctx,
                    label=label,
                    portfolio=portfolio_mode
                    or "basket" in q_low
                    or "portfolio" in q_low,
                )
            )

        if "rsi" in q_low and "snapshot" not in q_low:
            body = (
                "### RSI\n\n"
                "RSI (Relative Strength Index) measures momentum on a 0–100 scale. "
                "Above ~70 often reads overbought; below ~30 oversold. "
                "Pair it with trend and volume — not a standalone signal."
            )
        elif _is_narrow_topic(q_low, ("narrative", "catalyst", "why now", "theme")):
            cats = [c.strip() for c in categories.split(",") if c.strip()][:6]
            bullets = [f"* **{c}**" for c in cats] or [
                "* *Category tags unavailable in desk cache — refresh the coin and retry.*"
            ]
            about_line = about.split(";")[0].strip() if about else ""
            body = (
                f"### Narratives & catalysts — {label}\n\n"
                + (f"{about_line}\n\n" if about_line else "")
                + "Tied to these desk tags right now:\n"
                + "\n".join(bullets)
                + "\n\n*Live desk AI is briefly offline — this is a cache-based read, not a full brief.*"
            )
        elif _is_narrow_topic(q_low, ("risk", "watch-out", "watch out")):
            body = (
                f"### Risks — {label}\n\n"
                "* Volatility and meme/community flows can dominate tape\n"
                "* Liquidity shocks when volume fades\n"
                "* Unlock / float dynamics if supply expands\n"
                "* Narrative fatigue — cultural attention is the product\n\n"
                "*Live desk AI is briefly offline — treat this as a starter checklist.*"
            )
        elif "compare" in q_low or " vs " in q_low:
            body = (
                "### Comparison frame\n\n"
                "* Settlement security and real usage\n"
                "* Fee / liquidity depth\n"
                "* Developer and community activity\n"
                "* Supply and unlock schedule\n\n"
                "Price alone is a weak peer metric."
            )
        else:
            bits = [f"**{label}**"]
            if rank:
                bits.append(f"rank #{rank}")
            if chg24:
                bits.append(f"24h {chg24}%")
            head = " · ".join(bits)
            body = (
                f"### Desk note\n\n"
                f"{head}.\n\n"
                "Live research AI is briefly offline. Re-ask in a moment for a full desk answer, "
                "or open the coin page for fundamentals and tape."
            )

        return normalize_model_output(body)


def _ctx_field(context: str, key: str) -> str | None:
    if not context:
        return None
    m = re.search(
        rf"(?:^|[\s\-]){re.escape(key)}=([^\n]*?)(?=\s+\w[\w_]*=|$|\n)",
        context,
        re.I,
    )
    if not m:
        return None
    val = m.group(1).strip().strip("\"'")
    return val if val and val.lower() not in ("none", "null", "—", "-") else None


def _is_full_brief_request(q_low: str) -> bool:
    keys = (
        "full research",
        "full brief",
        "desk brief",
        "full desk",
        "complete report",
        "full report",
        "run a full",
        "research desk brief",
        "portfolio research desk",
        "### 1) snapshot",
        "### 1) basket snapshot",
    )
    return any(k in q_low for k in keys)


def _is_narrow_topic(q_low: str, needles: tuple[str, ...]) -> bool:
    """True when the ask is about a topic — not a pasted multi-section template."""
    if _is_full_brief_request(q_low):
        return False
    # Long structured prompts with many ### headings are briefs, not narrow Qs.
    if q_low.count("###") >= 3:
        return False
    return any(n in q_low for n in needles)


def _fmt_num(raw: str | None, *, prefix: str = "", suffix: str = "") -> str:
    if raw is None or raw == "":
        return "—"
    try:
        n = float(str(raw).replace(",", "").replace("%", "").strip())
    except ValueError:
        return f"{prefix}{raw}{suffix}".strip() or "—"
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        body = f"{n / 1_000_000_000:.2f}B"
    elif abs_n >= 1_000_000:
        body = f"{n / 1_000_000:.2f}M"
    elif abs_n >= 1_000:
        body = f"{n:,.2f}"
    elif abs_n >= 1:
        body = f"{n:.2f}"
    else:
        body = f"{n:.6g}"
    return f"{prefix}{body}{suffix}"


def _pct(raw: str | None) -> str:
    if raw is None or raw == "":
        return "—"
    try:
        n = float(str(raw).replace("%", "").replace(",", "").strip())
        sign = "+" if n > 0 else ""
        return f"{sign}{n:.2f}%"
    except ValueError:
        return str(raw)


def _fallback_full_desk_brief(
    ctx: str, *, label: str, portfolio: bool = False
) -> str:
    """Structured 7-section brief from desk cache when live AI is unavailable."""
    about = _ctx_field(ctx, "about") or ""
    about_line = about.split(";")[0].strip() if about else ""
    cats = [
        c.strip()
        for c in (_ctx_field(ctx, "categories") or "").split(",")
        if c.strip()
    ][:6]
    insight = _ctx_field(ctx, "insight") or ""
    risk = _ctx_field(ctx, "risk") or ""
    price = _fmt_num(_ctx_field(ctx, "price_usd"), prefix="$")
    mcap = _fmt_num(_ctx_field(ctx, "mcap"), prefix="$")
    fdv = _fmt_num(_ctx_field(ctx, "fdv"), prefix="$")
    vol = _fmt_num(_ctx_field(ctx, "volume_24h"), prefix="$")
    chg1h = _pct(_ctx_field(ctx, "change_1h"))
    chg24 = _pct(_ctx_field(ctx, "change_24h"))
    chg7d = _pct(_ctx_field(ctx, "change_7d"))
    chg30d = _pct(_ctx_field(ctx, "change_30d"))
    ath = _fmt_num(_ctx_field(ctx, "ath"), prefix="$")
    atl = _fmt_num(_ctx_field(ctx, "atl"), prefix="$")
    rank = _ctx_field(ctx, "rank") or "—"
    sentiment = _ctx_field(ctx, "sentiment") or "—"

    if portfolio:
        total_value = _fmt_num(_ctx_field(ctx, "total_value"), prefix="$")
        total_cost = _fmt_num(_ctx_field(ctx, "total_cost"), prefix="$")
        pnl = _fmt_num(_ctx_field(ctx, "pnl"), prefix="$")
        pnl_pct = _pct(_ctx_field(ctx, "pnl_pct"))
        basket_name = _ctx_field(ctx, "name") or label
        return (
            f"### 1) Basket Snapshot\n"
            f"{basket_name} — cache desk read while live AI is rate-limited.\n"
            f"Book snapshot from attached holdings only; re-ask shortly for a live write-up.\n\n"
            f"---\n\n"
            f"### 2) Holdings Tape\n"
            f"* **Basket Value**: {total_value}\n"
            f"* **Cost Basis**: {total_cost}\n"
            f"* **P&L**: {pnl} ({pnl_pct})\n\n"
            f"---\n\n"
            f"### 3) Concentration & Weights\n"
            f"* Review largest position weights in the attached holdings list.\n"
            f"* Single-name and narrative concentration dominate risk when AI is offline.\n\n"
            f"---\n\n"
            f"### 4) Performance Read\n"
            f"* P&L is driven by spot moves vs cost basis on the largest weights.\n"
            f"* Re-check 24h movers on each holding when the live model returns.\n\n"
            f"---\n\n"
            f"### 5) Narratives Across Names\n"
            f"* Shared themes come from overlapping categories across holdings.\n"
            f"* Treat this as a placeholder until the live desk brief runs.\n\n"
            f"---\n\n"
            f"### 6) Risks & Watch-Outs\n"
            f"* Concentration risk if one name is most of NAV\n"
            f"* Correlation spikes in risk-off tapes\n"
            f"* Stale prices if market data lags\n\n"
            f"---\n\n"
            f"### 7) What to Monitor Next\n"
            f"1. Re-run the full basket brief once live AI is available\n"
            f"2. Largest weight vs thesis\n"
            f"3. 24h P&L drivers\n"
            f"4. Liquidity on top holdings\n"
            f"5. Any unlock / event risk on concentrated names\n"
        )

    snap_bits = [
        f"{label} sits at {price} with market cap {mcap} (rank #{rank}).",
    ]
    if about_line:
        snap_bits.append(about_line)
    else:
        snap_bits.append(
            "Wrapped / liquid-staking style assets track underlying ETH staking exposure "
            "with DeFi composability as the product."
            if "steth" in label.lower() or "wsteth" in label.lower()
            else "Use the coin desk fundamentals and tape for the full picture while live AI recovers."
        )
    if insight:
        snap_bits.append(insight)

    cat_bullets = (
        "\n".join(f"* **{c}**" for c in cats)
        if cats
        else "* Category tags unavailable in desk cache"
    )

    return (
        f"### 1) Snapshot\n"
        + " ".join(snap_bits)
        + "\n\n"
        f"---\n\n"
        f"### 2) Market Tape\n"
        f"* **Spot Price**: {price}\n"
        f"* **Market Capitalization**: {mcap}\n"
        f"* **Fully Diluted Valuation (FDV)**: {fdv}\n"
        f"* **24-Hour Trading Volume**: {vol}\n"
        f"* **1-Hour**: {chg1h}\n"
        f"* **24-Hour**: {chg24}\n"
        f"* **7-Day**: {chg7d}\n"
        f"* **30-Day**: {chg30d}\n"
        f"* **All-Time High (ATH)**: {ath}\n"
        f"* **All-Time Low (ATL)**: {atl}\n"
        f"* Desk sentiment tag: {sentiment}\n\n"
        f"---\n\n"
        f"### 3) Trend & Technical Read\n"
        f"* 24h tape: {chg24}; 7d: {chg7d}; 30d: {chg30d}\n"
        f"* Bias follows recent multi-day direction until structure breaks\n"
        f"* Prefer levels from the coin chart — this cache brief has no live OHLC\n\n"
        f"---\n\n"
        f"### 4) Fundamentals\n"
        f"* What it is: {about_line or 'See coin page description for protocol detail'}\n"
        f"* Role in stack: composable claim on underlying exposure / liquidity wrapper\n"
        f"* Desk risk tag: {risk or '—'}\n\n"
        f"---\n\n"
        f"### 5) Narratives & Catalysts\n"
        f"{cat_bullets}\n\n"
        f"---\n\n"
        f"### 6) Risks & Watch-Outs\n"
        f"* Smart-contract / wrapper risk vs the underlying asset\n"
        f"* Peg / rate basis drift under stress\n"
        f"* Liquidity thinning on secondary venues\n"
        f"* Protocol or validator-set events that hit the underlying\n"
        f"* Regulatory framing around staking products\n\n"
        f"---\n\n"
        f"### 7) What to Monitor Next\n"
        f"1. Spot vs underlying / fair rate\n"
        f"2. 24h volume vs mcap (liquidity)\n"
        f"3. DeFi TVL / integration headlines for this wrapper\n"
        f"4. Major unlock or governance events on the underlying protocol\n"
        f"5. Re-run a live Alphora desk brief when model quota resets\n"
    )


def _read_error_body(resp: httpx.Response) -> str:
    try:
        # stream responses need read() before json in some httpx versions
        raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        return ((data.get("error") or {}).get("message") or "")[:300]
    except Exception:
        try:
            return (resp.text or "")[:300]
        except Exception:
            return ""


def _fallback_research_sections(
    name: str, trend_hint: str, ta: dict[str, Any]
) -> list[dict[str, Any]]:
    rsi_v = ta.get("rsi")
    rsi_bit = (
        f"RSI around {float(rsi_v):.0f}"
        if isinstance(rsi_v, (int, float))
        else "RSI unavailable"
    )
    return [
        {
            "key": "should_research",
            "title": "Should you research?",
            "bullets": [
                f"Yes if you care how {name} behaves when liquidity and narrative line up — not because a feed said “alpha.”",
                "Skim supply, volume, and category peers before you fall for a single candle.",
            ],
        },
        {
            "key": "trend",
            "title": "Trend read",
            "bullets": [
                f"Near-term lean looks {trend_hint} from recent price action.",
                f"{rsi_bit}; MACD note: {ta.get('macd_signal') or 'mixed'}.",
                "A trend without volume confirmation is just a pretty slope.",
            ],
        },
        {
            "key": "risks",
            "title": "Risks",
            "bullets": [
                "Volatility can rewrite the story in one session.",
                "Narrative rotation and unlock overhang can hit harder than the chart admits.",
                "Protocol / custody / regulatory risk still sits under every position.",
            ],
        },
        {
            "key": "opportunities",
            "title": "Opportunities",
            "bullets": [
                "Relative strength vs category peers when the tape is quiet.",
                "Clearer utility (fees, staking, real governance) that makes the thesis less vibes-only.",
                "Catalyst windows: listings, upgrades, usage milestones worth verifying on-chain.",
            ],
        },
        {
            "key": "monitor_next",
            "title": "Watch next",
            "bullets": [
                "Support / resistance from the technical map — and whether volume agrees.",
                "Any sudden supply unlock chatter or governance fights.",
                "Whether the 24h move sticks after the first burst of attention fades.",
            ],
        },
    ]


def _parse_research_markdown(text: str) -> dict[str, Any] | None:
    import re

    if not text or "##" not in text:
        return None

    section_map = [
        ("should you research", "should_research", "Should you research?"),
        ("trend", "trend", "Trend read"),
        ("risk", "risks", "Risks"),
        ("opportunit", "opportunities", "Opportunities"),
        ("watch", "monitor_next", "Watch next"),
        ("monitor", "monitor_next", "Watch next"),
    ]

    chunks = re.split(r"\n(?=##\s*)", text.strip())
    found: dict[str, list[str]] = {}
    titles: dict[str, str] = {}

    for chunk in chunks:
        lines = [ln.strip() for ln in chunk.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        header = re.sub(r"^#+\s*", "", lines[0]).strip().lower()
        bullets = []
        for ln in lines[1:]:
            if ln.startswith(("-", "•", "*")):
                bullets.append(ln.lstrip("-•* ").strip())
            elif len(ln) > 24 and not ln.startswith("#"):
                bullets.append(ln)
        if not bullets:
            continue
        for needle, key, title in section_map:
            if needle in header and key not in found:
                found[key] = bullets[:4]
                titles[key] = title
                break

    if len(found) < 3:
        return None

    order = ["should_research", "trend", "risks", "opportunities", "monitor_next"]
    sections = [
        {"key": k, "title": titles.get(k, k), "bullets": found[k]}
        for k in order
        if k in found
    ]
    flat = {k: "\n".join(found[k]) for k in found}
    return {**flat, "sections": sections, "full": ""}


ai_service = AIService()
