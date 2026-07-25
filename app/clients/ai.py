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
        self.groq_api_key = getattr(Config, "GROQ_API_KEY", "") or ""
        self.groq_model = getattr(Config, "GROQ_MODEL", "") or "llama-3.3-70b-versatile"
        # Primary first, then configured fallbacks (deduped).
        seen: set[str] = set()
        models: list[str] = []
        for m in [self.model, *getattr(Config, "GEMINI_MODEL_FALLBACKS", [])]:
            if m and m not in seen:
                seen.add(m)
                models.append(m)
        self.models = models or [self.model or "gemini-2.5-flash"]

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.groq_api_key)

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

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
    ) -> str:
        system = (
            "You are Lumen Keel AI — a premium crypto research desk assistant. "
            "Be clear, calm, and educational. Never give personalized financial advice "
            "or tell the user to buy/sell. Prefer structured answers with short headings "
            "and bullets. Use only provided market context for numbers; if a figure is "
            "missing, say so instead of inventing it."
        )
        if research_mode:
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
            import logging

            log = logging.getLogger(__name__)
            if self.groq_enabled:
                log.warning(
                    "Gemini rate-limited (%s); trying free Groq fallback", exc
                )
                try:
                    return self._chat_groq(full)
                except Exception as groq_exc:
                    log.warning("Groq fallback failed (%s); offline reply", groq_exc)
            else:
                log.warning(
                    "Gemini rate-limited (%s); set GROQ_API_KEY for free fallback",
                    exc,
                )
            return self._fallback_reply(last, context, reason="rate_limit")
        except Exception as exc:
            import logging

            log = logging.getLogger(__name__)
            if self.groq_enabled and not self.gemini_enabled:
                try:
                    return self._chat_groq(full)
                except Exception as groq_exc:
                    log.warning("Groq error (%s); offline reply", groq_exc)
            log.warning("Gemini error (%s); using offline reply", exc)
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

    def _chat(self, messages: list[dict[str, str]], retries: int = 2) -> str:
        if not self.gemini_enabled:
            if self.groq_enabled:
                return self._chat_groq(messages)
            raise RuntimeError("No AI provider configured")

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

        last_error: Exception | None = None
        rate_limit_detail = ""

        for model in self.models:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            for attempt in range(retries):
                try:
                    with httpx.Client(timeout=90.0) as client:
                        resp = client.post(
                            url, params={"key": self.api_key}, json=payload
                        )

                    if resp.status_code == 429:
                        try:
                            rate_limit_detail = (
                                (resp.json().get("error") or {}).get("message") or ""
                            )
                        except Exception:
                            rate_limit_detail = (resp.text or "")[:200]
                        # Try next model immediately — this project's free quota for
                        # gemini-2.0-flash is often limit:0 while other Flash models work.
                        import logging

                        logging.getLogger(__name__).warning(
                            "Gemini model %s rate-limited; trying next fallback", model
                        )
                        break

                    if resp.status_code == 404:
                        # Model not available for this key — try next.
                        import logging

                        logging.getLogger(__name__).warning(
                            "Gemini model %s not found; trying next fallback", model
                        )
                        break

                    if resp.status_code in (400, 401, 403):
                        detail = ""
                        try:
                            detail = (resp.json().get("error") or {}).get("message") or ""
                        except Exception:
                            detail = (resp.text or "")[:300]
                        raise RuntimeError(
                            f"Gemini HTTP {resp.status_code}: {detail or resp.reason_phrase}"
                        )

                    if resp.status_code >= 500:
                        if attempt < retries - 1:
                            time.sleep(1.0 * (attempt + 1))
                            continue
                        resp.raise_for_status()

                    resp.raise_for_status()
                    data = resp.json()
                    prompt_feedback = data.get("promptFeedback") or {}
                    if prompt_feedback.get("blockReason"):
                        raise RuntimeError(
                            f"Gemini blocked prompt: {prompt_feedback.get('blockReason')}"
                        )
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise RuntimeError("Gemini returned no candidates")
                    parts = ((candidates[0].get("content") or {}).get("parts")) or []
                    text = "".join(part.get("text", "") for part in parts).strip()
                    if not text:
                        finish = candidates[0].get("finishReason") or "unknown"
                        raise RuntimeError(
                            f"Gemini returned empty text (finish={finish})"
                        )
                    if model != self.model:
                        import logging

                        logging.getLogger(__name__).info(
                            "Gemini reply served via fallback model %s", model
                        )
                    return text
                except AIRateLimitError:
                    raise
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response is not None and exc.response.status_code == 429:
                        break
                    if attempt < retries - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    if attempt < retries - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    # Try next model on transport failure
                    break

        if rate_limit_detail:
            # Prefer free Groq before surfacing rate-limit to chat().
            if self.groq_enabled:
                import logging

                logging.getLogger(__name__).warning(
                    "All Gemini models rate-limited; trying Groq (%s)",
                    self.groq_model,
                )
                return self._chat_groq(messages)
            raise AIRateLimitError(rate_limit_detail)
        if last_error:
            raise last_error
        raise RuntimeError("Gemini request failed")

    def _chat_groq(self, messages: list[dict[str, str]]) -> str:
        """Free Groq OpenAI-compatible chat (used when Gemini quota is exhausted)."""
        if not self.groq_api_key:
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

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": self.groq_model,
            "messages": payload_messages,
            "temperature": 0.4,
        }
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

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
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("Groq returned empty text")

        import logging

        logging.getLogger(__name__).info(
            "AI reply served via free Groq model %s", self.groq_model
        )
        return text

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
