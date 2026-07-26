from __future__ import annotations

import json
import logging
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
        self.api_key = getattr(Config, "GROQ_API_KEY", "") or ""
        self.model = getattr(Config, "GROQ_MODEL", "") or "openai/gpt-oss-120b"
        self.temperature = float(getattr(Config, "GROQ_TEMPERATURE", 1) or 1)
        self.max_completion_tokens = int(
            getattr(Config, "GROQ_MAX_COMPLETION_TOKENS", 2048) or 2048
        )
        self.top_p = float(getattr(Config, "GROQ_TOP_P", 1) or 1)
        self.reasoning_effort = (
            getattr(Config, "GROQ_REASONING_EFFORT", "") or "medium"
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
        system = (
            "You are Alphora AI — a premium crypto research desk assistant from Alphora Labs. "
            "Be clear, calm, and educational. Never give personalized financial advice "
            "or tell the user to buy/sell. Prefer structured answers with short headings "
            "and bullets. Use only provided market context for numbers; if a figure is "
            "missing, say so instead of inventing it."
        )
        if portfolio_mode:
            system += (
                "\n\nWhen the user asks for research on an attached portfolio basket, ALWAYS "
                "respond in this EXACT markdown structure (same headings every time, no extras, "
                "no intro/outro outside sections):\n\n"
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
                "No buy/sell advice. Use only provided holdings context for numbers."
            )
        elif research_mode:
            system += (
                "\n\nWhen the user asks for research on the attached coin, ALWAYS respond "
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
            system += f"\n\nContext:\n{context}"

        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        if not self.enabled:
            return self._fallback_reply(last, context, reason="offline")

        full = [{"role": "system", "content": system}, *messages]
        try:
            return self._chat(full)
        except AIRateLimitError as exc:
            log.warning("Groq rate-limited (%s); offline reply", exc)
            return self._fallback_reply(last, context, reason="rate_limit")
        except Exception as exc:
            log.warning("Groq error (%s); offline reply", exc)
            return self._fallback_reply(last, context, reason="error")

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

    def _supports_reasoning_effort(self) -> bool:
        # Only gpt-oss family accepts reasoning_effort on Groq today.
        m = (self.model or "").lower()
        return "gpt-oss" in m or m.startswith("openai/gpt-oss")

    def _chat(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")

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

        use_reasoning = bool(self.reasoning_effort) and self._supports_reasoning_effort()
        try:
            text = self._groq_complete(payload_messages, use_reasoning=use_reasoning)
        except RuntimeError as exc:
            msg = str(exc).lower()
            if use_reasoning and "reasoning_effort" in msg:
                log.warning(
                    "Groq rejected reasoning_effort for %s; retrying without it",
                    self.model,
                )
                text = self._groq_complete(payload_messages, use_reasoning=False)
            else:
                raise

        cleaned = normalize_model_output(text)
        if not cleaned:
            raise RuntimeError("Groq returned empty text after normalize")

        log.info("AI reply served via Groq model %s", self.model)
        return cleaned

    def _groq_complete(
        self, payload_messages: list[dict[str, str]], *, use_reasoning: bool
    ) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
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
        self, question: str, context: str | None, reason: str = "offline"
    ) -> str:
        """Local research reply when Groq is unavailable."""
        q = (question or "").lower()
        _ = reason

        if "rsi" in q:
            body = (
                "RSI (Relative Strength Index) measures momentum on a 0–100 scale. "
                "Readings above 70 often signal overbought conditions; below 30 oversold. "
                "Use it with trend and volume, not in isolation."
            )
        elif "compare" in q or " vs " in q:
            body = (
                "When comparing networks, weigh settlement security, developer activity, "
                "fee dynamics, liquidity depth, and real usage—not just price. "
                "Context for this chat may include market data when a coin is attached."
            )
        elif "risk" in q:
            body = (
                "Key risks typically include volatility, smart-contract or protocol risk, "
                "liquidity shocks, regulatory uncertainty, and concentration in holders or unlocks."
            )
        else:
            ctx = f"\n\nAvailable context:\n{context}" if context else ""
            body = (
                "Here’s a structured take: clarify the thesis, check liquidity and unlocks, "
                "map catalysts, and size risk before acting. "
                f"You asked: “{question}”.{ctx}"
            )

        return body.strip()


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
