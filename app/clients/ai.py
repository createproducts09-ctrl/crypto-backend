from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import Config


FALLBACK_INSIGHTS = [
    "Momentum is improving with increasing trading volume.",
    "Price action is consolidating near recent support.",
    "Volatility remains elevated; size positions carefully.",
    "Trend strength looks constructive on the medium timeframe.",
    "Liquidity is healthy relative to recent market activity.",
]


class AIRateLimitError(RuntimeError):
    """Gemini quota / rate limit exceeded."""


class AIService:
    def __init__(self):
        self.api_key = Config.GEMINI_API_KEY
        self.model = Config.GEMINI_MODEL

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

    def chat(self, messages: list[dict[str, str]], context: str | None = None) -> str:
        system = (
            "You are a premium crypto research assistant for a fintech app. "
            "Be clear, calm, and educational. Never give personalized financial advice. "
            "Prefer structured, concise answers."
        )
        if context:
            system += f"\n\nContext:\n{context}"

        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        if not self.enabled:
            return self._fallback_reply(last, context, reason="offline")

        full = [{"role": "system", "content": system}, *messages]
        try:
            return self._chat(full)
        except AIRateLimitError:
            import logging

            logging.getLogger(__name__).warning("Gemini rate-limited; using offline reply")
            return self._fallback_reply(last, context, reason="rate_limit")
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Gemini error (%s); using offline reply", exc)
            return self._fallback_reply(last, context, reason="error")

    def summarize_news(self, title: str, body: str) -> str:
        if not self.enabled:
            text = (body or title or "").strip()
            return (text[:180] + "…") if len(text) > 180 else text
        prompt = f"Summarize this crypto news in 1-2 neutral sentences.\nTitle: {title}\nBody: {body[:1500]}"
        try:
            return self._chat([{"role": "user", "content": prompt}]).strip()
        except Exception:
            return (body or title)[:180]

    def research_summary(self, coin: dict[str, Any], ta: dict[str, Any] | None = None) -> dict[str, Any]:
        name = coin.get("name", "this project")
        symbol = coin.get("symbol") or ""
        change = coin.get("price_change_percentage_24h") or 0
        trend_hint = "bullish" if change > 1 else "bearish" if change < -1 else "sideways"
        ta = ta or {}

        if self.enabled:
            prompt = (
                f"You are writing a crisp research brief for {name} ({symbol}) inside a mobile crypto app.\n"
                "Tone: sharp, calm, Gen-Z friendly but professional. No hype. No buy/sell advice.\n"
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
                f"TA hint: trend={ta.get('trend')}, rsi={ta.get('rsi')}, macd={ta.get('macd_signal')}."
            )
            try:
                text = self._chat([{"role": "user", "content": prompt}])
                parsed = _parse_research_markdown(text)
                if parsed:
                    return parsed
                return {"full": text, "sections": _fallback_research_sections(name, trend_hint, ta)}
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

    def _chat(self, messages: list[dict[str, str]], retries: int = 3) -> str:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for message in messages:
            role = message.get("role") or "user"
            text = (message.get("content") or "").strip()
            if not text:
                continue
            if role == "system":
                system_parts.append(text)
                continue
            gemini_role = "user" if role == "user" else "model"
            # Gemini requires alternating user/model; merge consecutive same roles
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"][0]["text"] += f"\n\n{text}"
            else:
                contents.append({"role": gemini_role, "parts": [{"text": text}]})

        if not contents:
            raise ValueError("No chat content provided")

        # Gemini conversations should start with a user turn
        if contents[0]["role"] != "user":
            contents.insert(0, {"role": "user", "parts": [{"text": "Continue."}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.4},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, params={"key": self.api_key}, json=payload)

                if resp.status_code == 429:
                    # Honor Retry-After when present; otherwise exponential backoff
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else (1.5 * (2**attempt))
                    except ValueError:
                        wait = 1.5 * (2**attempt)
                    wait = min(max(wait, 0.5), 12.0)
                    if attempt < retries - 1:
                        time.sleep(wait)
                        continue
                    raise AIRateLimitError("Gemini rate limit exceeded")

                if resp.status_code >= 500:
                    if attempt < retries - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    raise RuntimeError("Gemini returned no candidates")
                parts = ((candidates[0].get("content") or {}).get("parts")) or []
                text = "".join(part.get("text", "") for part in parts).strip()
                if not text:
                    raise RuntimeError("Gemini returned empty text")
                return text
            except AIRateLimitError:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response is not None and exc.response.status_code == 429:
                    raise AIRateLimitError("Gemini rate limit exceeded") from exc
                if attempt < retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Gemini request failed")

    def _fallback_reply(self, question: str, context: str | None, reason: str = "offline") -> str:
        """Local research reply when Gemini is unavailable.

        Do not surface provider/rate-limit jargon to the user — they just want an answer.
        """
        q = (question or "").lower()
        _ = reason  # kept for logging callers; not shown in UI

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


def _fallback_research_sections(name: str, trend_hint: str, ta: dict[str, Any]) -> list[dict[str, Any]]:
    rsi_v = ta.get("rsi")
    rsi_bit = f"RSI around {float(rsi_v):.0f}" if isinstance(rsi_v, (int, float)) else "RSI unavailable"
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
                f"Support / resistance from the technical map — and whether volume agrees.",
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
