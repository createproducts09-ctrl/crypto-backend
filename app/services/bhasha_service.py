from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timedelta, timezone
from time import time
from typing import Any

from app.clients.ai import ai_service
from app.clients.sarvam import SarvamError, sarvam_client
from app.extensions import db

log = logging.getLogger(__name__)

LANGUAGES: list[dict[str, str]] = [
    {"id": "hi", "bcp47": "hi-IN", "label": "हिन्दी", "english": "Hindi"},
    {"id": "ta", "bcp47": "ta-IN", "label": "தமிழ்", "english": "Tamil"},
    {"id": "te", "bcp47": "te-IN", "label": "తెలుగు", "english": "Telugu"},
    {"id": "bn", "bcp47": "bn-IN", "label": "বাংলা", "english": "Bengali"},
    {"id": "mr", "bcp47": "mr-IN", "label": "मराठी", "english": "Marathi"},
    {"id": "gu", "bcp47": "gu-IN", "label": "ગુજરાતી", "english": "Gujarati"},
    {"id": "kn", "bcp47": "kn-IN", "label": "ಕನ್ನಡ", "english": "Kannada"},
    {"id": "ml", "bcp47": "ml-IN", "label": "മലയാളം", "english": "Malayalam"},
    {"id": "pa", "bcp47": "pa-IN", "label": "ਪੰਜਾਬੀ", "english": "Punjabi"},
    {"id": "en", "bcp47": "en-IN", "label": "English", "english": "English"},
]

_LANG_BY_ID = {row["id"]: row for row in LANGUAGES}
_LANG_BY_BCP = {row["bcp47"]: row for row in LANGUAGES}

_CACHE_TTL = timedelta(hours=6)
_RATE: dict[tuple[str, str], list[float]] = {}


def status() -> dict[str, Any]:
    return {
        "enabled": sarvam_client.enabled,
        "provider": "sarvam",
        "languages": LANGUAGES,
    }


def resolve_language(raw: str | None) -> dict[str, str]:
    key = (raw or "hi").strip()
    if key in _LANG_BY_ID:
        return _LANG_BY_ID[key]
    if key in _LANG_BY_BCP:
        return _LANG_BY_BCP[key]
    short = key.split("-")[0].lower()
    if short in _LANG_BY_ID:
        return _LANG_BY_ID[short]
    return _LANG_BY_ID["hi"]


def allow_request(ip: str, bucket: str, limit: int, window: float = 3600) -> bool:
    now = time()
    key = (ip or "anon", bucket)
    kept = [t for t in _RATE.get(key, []) if now - t < window]
    if len(kept) >= limit:
        _RATE[key] = kept
        return False
    kept.append(now)
    _RATE[key] = kept
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_get(key: str) -> dict[str, Any] | None:
    try:
        doc = db.bhasha_cache.find_one({"key": key})
    except Exception:
        return None
    if not doc:
        return None
    exp = doc.get("expires_at")
    if isinstance(exp, datetime):
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now():
            return None
    payload = doc.get("payload")
    return payload if isinstance(payload, dict) else None


def _cache_set(key: str, payload: dict[str, Any]) -> None:
    try:
        db.bhasha_cache.update_one(
            {"key": key},
            {
                "$set": {
                    "key": key,
                    "payload": payload,
                    "updated_at": _now(),
                    "expires_at": _now() + _CACHE_TTL,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        log.warning("Bhasha cache write skipped: %s", exc)


def _fmt_num(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.2f}K"
    if abs(n) >= 1:
        return f"{n:,.2f}"
    return f"{n:.4f}"


def _fmt_pct(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.2f}%"


def _english_pack(coin_id: str) -> dict[str, Any]:
    from app.services import research_service

    pack = None
    try:
        pack = research_service.full_research(coin_id, force=False, with_ai=False)
    except Exception as exc:
        log.warning("Research pack failed for %s: %s", coin_id, exc)
    if not pack:
        name = coin_id.replace("-", " ").title()
        return {
            "coin_id": coin_id,
            "name": name,
            "symbol": coin_id[:4].upper(),
            "image": "",
            "research_score": None,
            "price": None,
            "chg_24h": None,
            "snapshot": f"{name} is on the Alphora desk. Live market fields were unavailable — treat this as a language demo, then open the full brief.",
            "risk": "Do not size from a vernacular preview alone. Confirm liquidity, unlocks, and custody yourself.",
            "monitor": "Re-open this coin on the desk when market data is back and watch 24h volume, FDV gap, and narrative crowding.",
            "english": {
                "snapshot": f"{name} research preview is limited until market data loads.",
                "risk": "Confirm liquidity and unlocks before any thesis.",
                "monitor": "Watch volume, FDV, and the active desk thesis.",
            },
        }
    coin = pack.get("coin") or {}
    market = pack.get("market") or {}
    so = pack.get("so_what") or {}
    name = coin.get("name") or coin_id
    symbol = (coin.get("symbol") or "").upper()
    headline = so.get("headline") or pack.get("why_interesting") or ""
    concern = pack.get("biggest_concern") or ""
    claims = []
    for item in so.get("claims") or []:
        if isinstance(item, dict) and item.get("text"):
            claims.append(str(item["text"]))
        elif isinstance(item, str):
            claims.append(item)
    snapshot = " ".join(
        [
            f"{name} ({symbol}) trades at {_fmt_num(market.get('price') or coin.get('current_price'))}.",
            f"24h {_fmt_pct(market.get('chg_24h') or coin.get('price_change_percentage_24h'))}.",
            f"Market cap {_fmt_num(market.get('market_cap') or coin.get('market_cap'))}, "
            f"FDV {_fmt_num(market.get('fdv') or coin.get('fully_diluted_valuation'))}.",
            f"Research score {pack.get('research_score') or '—'}/100.",
            headline,
        ]
    ).strip()
    risk = concern or "Read unlocks, liquidity, and narrative crowding before sizing."
    monitor = "Watch 24h volume vs market cap, unlock calendar, and whether the active thesis still holds."
    if claims:
        snapshot = f"{snapshot} {' '.join(claims[:2])}"
    return {
        "coin_id": coin_id,
        "name": name,
        "symbol": symbol,
        "image": coin.get("image") or "",
        "research_score": pack.get("research_score"),
        "price": market.get("price") or coin.get("current_price"),
        "chg_24h": market.get("chg_24h") or coin.get("price_change_percentage_24h"),
        "snapshot": snapshot,
        "risk": risk,
        "monitor": monitor,
        "english": {
            "snapshot": snapshot,
            "risk": risk,
            "monitor": monitor,
        },
    }


def _rewrite(text: str, language: dict[str, str]) -> str:
    if language["id"] == "en":
        return text
    prompt = (
        f"Rewrite the following crypto research notes in {language['english']} "
        f"({language['label']}). Keep tickers, USD prices, percentages, FDV, "
        "dates, and proper nouns unchanged. Do not add advice or disclaimers. "
        "Do not add headings the source does not have. Neutral research tone.\n\n"
        f"{text}"
    )
    if sarvam_client.enabled:
        try:
            return sarvam_client.chat(
                [
                    {
                        "role": "system",
                        "content": "You are Alphora Bhasha, a crypto research desk for Indian languages.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1200,
            )
        except Exception as exc:
            log.warning("Sarvam rewrite failed: %s", exc)
        try:
            return sarvam_client.translate(
                text,
                source_language_code="en-IN",
                target_language_code=language["bcp47"],
            )
        except Exception as exc:
            log.warning("Sarvam translate failed: %s", exc)
    if ai_service.enabled:
        try:
            return ai_service._chat(  # noqa: SLF001 — reuse Groq for fallback rewrite
                [
                    {
                        "role": "system",
                        "content": "Rewrite research notes. Keep numbers and tickers. No preamble.",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception as exc:
            log.warning("Groq rewrite failed: %s", exc)
    return text


def _localize_sections(pack: dict[str, Any], language: dict[str, str]) -> dict[str, Any]:
    if language["id"] == "en":
        return {
            "snapshot": pack["snapshot"],
            "risk": pack["risk"],
            "monitor": pack["monitor"],
            "source": "research",
        }
    blob = (
        f"SNAPSHOT: {pack['snapshot']}\n"
        f"RISK: {pack['risk']}\n"
        f"MONITOR: {pack['monitor']}"
    )
    rewritten = _rewrite(blob, language)
    parsed = _parse_sections(rewritten, pack)
    return {**parsed, "source": "sarvam" if sarvam_client.enabled else "fallback"}


def _parse_sections(text: str, fallback: dict[str, Any]) -> dict[str, str]:
    snapshot = fallback["snapshot"]
    risk = fallback["risk"]
    monitor = fallback["monitor"]
    parts = re.split(r"(?i)\b(SNAPSHOT|RISK|MONITOR)\s*:\s*", text or "")
    if len(parts) >= 3:
        mapped: dict[str, str] = {}
        i = 1
        while i + 1 < len(parts):
            mapped[parts[i].upper()] = parts[i + 1].strip()
            i += 2
        snapshot = mapped.get("SNAPSHOT") or snapshot
        risk = mapped.get("RISK") or risk
        monitor = mapped.get("MONITOR") or monitor
    return {"snapshot": snapshot, "risk": risk, "monitor": monitor}


def brief(coin_id: str, language_id: str, *, question: str | None = None) -> dict[str, Any]:
    language = resolve_language(language_id)
    coin_id = (coin_id or "bitcoin").strip().lower()
    q = (question or "").strip()
    cache_key = f"brief:{coin_id}:{language['id']}:{q[:80]}"
    cached = _cache_get(cache_key)
    if cached:
        return {**cached, "cached": True}

    pack = _english_pack(coin_id)
    if q:
        pack["snapshot"] = f"Question: {q}\n{pack['snapshot']}"
        localized = _localize_sections(pack, language)
        if language["id"] != "en":
            answer = _rewrite(
                f"Answer this research question in {language['english']} using the notes. "
                "Keep numbers and tickers. Three short sections: SNAPSHOT, RISK, MONITOR.\n"
                f"Question: {q}\nNotes:\n{pack['english']['snapshot']}\n{pack['english']['risk']}",
                language,
            )
            localized = _parse_sections(answer, {**pack, **localized})
            localized["source"] = "sarvam" if sarvam_client.enabled else "fallback"
    else:
        localized = _localize_sections(pack, language)

    spoken = " ".join(
        [localized["snapshot"], localized["risk"], localized["monitor"]]
    )
    result = {
        "coin_id": pack["coin_id"],
        "name": pack["name"],
        "symbol": pack["symbol"],
        "image": pack["image"],
        "research_score": pack["research_score"],
        "price": pack["price"],
        "chg_24h": pack["chg_24h"],
        "language": language,
        "snapshot": localized["snapshot"],
        "risk": localized["risk"],
        "monitor": localized["monitor"],
        "spoken": spoken,
        "provider": "sarvam" if sarvam_client.enabled else "alphora",
        "cached": False,
        "research_only": True,
    }
    if not q:
        _cache_set(cache_key, result)
    return result


def ask(
    question: str,
    language_id: str,
    *,
    coin_id: str | None = None,
    user_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    language = resolve_language(language_id)
    question = (question or "").strip()
    if not question:
        raise ValueError("Question required")

    context = ""
    coin_meta: dict[str, Any] = {}
    if coin_id:
        try:
            pack = _english_pack(coin_id)
            coin_meta = {
                "coin_id": pack["coin_id"],
                "name": pack["name"],
                "symbol": pack["symbol"],
            }
            context = (
                f"Coin: {pack['name']} ({pack['symbol']})\n"
                f"{pack['english']['snapshot']}\n"
                f"Risk: {pack['english']['risk']}\n"
                f"Monitor: {pack['english']['monitor']}"
            )
        except Exception as exc:
            log.warning("Bhasha ask context failed: %s", exc)

    system = (
        "You are Alphora Bhasha, the Indic research desk for Alphora Labs. "
        f"Reply in {language['english']} ({language['label']}). "
        "Give a structured crypto research brief: Snapshot, Risk, Monitor. "
        "Keep tickers, prices, percentages, and dates in original form. "
        "Research only — not financial advice. Be concise."
    )
    user = question if not context else f"{question}\n\nGrounding:\n{context}"
    reply = ""
    provider = "fallback"
    last_error: Exception | None = None
    if sarvam_client.enabled:
        try:
            reply = sarvam_client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            provider = "sarvam"
        except Exception as exc:
            last_error = exc
            log.warning("Sarvam ask failed: %s", exc)
    if not reply:
        try:
            reply = ai_service._chat(  # noqa: SLF001
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            if reply:
                provider = "sarvam" if sarvam_client.enabled else "groq"
        except Exception as exc:
            last_error = last_error or exc
            log.warning("Ask fallback failed: %s", exc)
    if not reply:
        if isinstance(last_error, SarvamError):
            raise last_error
        raise SarvamError(
            str(last_error) if last_error else "Sarvam returned an empty brief",
            status=502,
        )

    persisted_thread = None
    if user_id:
        persisted_thread = _persist_ask(
            user_id, thread_id, question, reply, coin_id
        )

    return {
        "reply": reply,
        "language": language,
        "provider": provider,
        "thread_id": persisted_thread,
        **coin_meta,
        "research_only": True,
    }


ASSISTANT_SYSTEM = """You are Alphora, the on-site assistant for Alphora Labs (alphoralabs.com).
You ONLY discuss:
- Alphora Labs: Discover swipe, Ask desk, research scores, public /crypto pages, baskets & P&L, Pulse, News, Bhasha Indic voice, pricing (free vs Keel), research-only (not custody, not brokerage).
- Cryptocurrency research: tokens, market structure, tokenomics, FDV vs float, narratives, risks, how to read a tape.

Hard rules:
- If the user asks about anything else (coding, politics, medical, general trivia, other products), refuse in one short sentence and steer back to Alphora or crypto research.
- Never give personalized buy/sell advice. Education and research only.
- Keep replies short. Prefer markdown: one lead sentence, then 2–4 bullets. No wall of prose.
- Bold product names like **Ask Desk**, **Baskets & P&L**, **Discover**.
- Match the user's language when they write in an Indian language or Hinglish.
- Do not invent live prices. If you need a number, say they should open the Alphora desk or public research page.
"""

_ASSISTANT_MEM: dict[str, tuple[float, str]] = {}
_ASSISTANT_MEM_TTL = 6 * 3600


def _norm_question(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def _instant_assistant_reply(question: str) -> str | None:
    q = _norm_question(question)
    if re.search(r"what is alphora|who (is|are) alphora|about alphora|alphora labs\??$", q):
        return (
            "**Alphora Labs** is a crypto research desk — not a broker or custody app.\n\n"
            "- **Discover** — swipe markets and save names\n"
            "- **Ask Desk** — structured token briefs and research scores\n"
            "- **Baskets & P&L** — track a thesis book\n"
            "- **Pulse / News** — tape and headlines\n"
            "- **Bhasha** — Indic voice via Sarvam\n\n"
            "Research only. Not financial advice."
        )
    if re.search(r"how (do i |to )?research|research a token|token analysis|research (desk|score)", q):
        return (
            "Open **Ask Desk** or a public **/crypto** page and run a brief.\n\n"
            "- Check **research score**, spot vs **FDV**, and float\n"
            "- Read narratives and risks — not a buy/sell call\n"
            "- Save the name to a **basket** if you want P&L context later\n\n"
            "Need live numbers? Open the desk or the public research page."
        )
    if re.search(r"(basket|p&l|pnl|portfolio)", q) and re.search(r"(how|work|track|what|mean)", q):
        return (
            "**Baskets & P&L** group tokens you are researching into one book.\n\n"
            "- Add names with a cost basis to see unrealized P&L\n"
            "- Weights show concentration — one name or one narrative\n"
            "- This is a research tracker, not an exchange balance\n\n"
            "Open **Portfolio** to build a basket. Research only."
        )
    if re.search(r"(pricing|keel|free plan|upgrade|cost|paid)", q):
        return (
            "Alphora has a free desk and **Keel** for more Ask volume.\n\n"
            "- Free — Discover, public research pages, limited Ask\n"
            "- **Keel** — higher Ask limits and the full research desk\n\n"
            "Open **Pricing** in the app. Still research only — no custody."
        )
    return None


def assistant(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        raise ValueError("Question required")
    instant = _instant_assistant_reply(question)
    if instant:
        return {
            "reply": instant,
            "provider": "alphora",
            "model": "instant",
            "research_only": True,
        }
    cache_key = _norm_question(question)
    follow_up = bool(history)
    if not follow_up:
        hit = _ASSISTANT_MEM.get(cache_key)
        if hit and time() - hit[0] < _ASSISTANT_MEM_TTL:
            return {
                "reply": hit[1],
                "provider": "cache",
                "model": sarvam_client.chat_model,
                "research_only": True,
            }
    messages: list[dict[str, str]] = [{"role": "system", "content": ASSISTANT_SYSTEM}]
    for item in (history or [])[-8:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    reply = sarvam_client.chat(messages, temperature=0.2, max_tokens=512)
    if not follow_up and reply:
        _ASSISTANT_MEM[cache_key] = (time(), reply)
    return {
        "reply": reply,
        "provider": "sarvam",
        "model": sarvam_client.chat_model,
        "research_only": True,
    }


def transcribe(
    audio_b64: str,
    *,
    language_id: str | None = None,
    mime: str = "audio/webm",
    filename: str = "clip.webm",
) -> dict[str, Any]:
    if not sarvam_client.enabled:
        raise SarvamError("Sarvam is not configured", status=503)
    raw = _decode_audio(audio_b64)
    language = resolve_language(language_id) if language_id else None
    result = sarvam_client.transcribe(
        raw,
        filename=filename,
        mime=mime,
        language_code=language["bcp47"] if language and language["id"] != "en" else None,
        mode="transcribe",
    )
    detected = resolve_language(result.get("language_code") or (language or {}).get("id"))
    return {
        "transcript": result.get("transcript") or "",
        "language": detected,
        "provider": "sarvam",
    }


_INDIC_RE = re.compile(
    r"[\u0900-\u097F\u0980-\u09FF\u0A00-\u0A7F\u0A80-\u0AFF"
    r"\u0B00-\u0B7F\u0B80-\u0BFF\u0C00-\u0C7F\u0C80-\u0CFF"
    r"\u0D00-\u0D7F]"
)


def _looks_latin_english(text: str) -> bool:
    if _INDIC_RE.search(text or ""):
        return False
    return len(re.findall(r"[A-Za-z]", text or "")) >= 8


def _for_speech(text: str, language: dict[str, str]) -> str:
    spoken = _plain_for_speech(text)
    if language["id"] == "en" or not spoken:
        return spoken
    if not _looks_latin_english(spoken):
        return spoken
    cache_key = f"speech:{language['id']}:{hash(spoken[:800])}"
    cached = _cache_get(cache_key)
    if isinstance(cached, dict) and cached.get("text"):
        return _plain_for_speech(str(cached["text"]))
    translated = spoken
    try:
        translated = (
            sarvam_client.translate(
                spoken,
                source_language_code="en-IN",
                target_language_code=language["bcp47"],
            )
            or spoken
        )
    except Exception as exc:
        log.warning("Speech translate failed: %s", exc)
        try:
            translated = (
                sarvam_client.chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                f"Translate into {language['english']} ({language['label']}). "
                                "Keep tickers, USD amounts, and percentages. "
                                "Output only the translation — no English leftover."
                            ),
                        },
                        {"role": "user", "content": spoken},
                    ],
                    temperature=0.1,
                    max_tokens=800,
                )
                or spoken
            )
        except Exception as exc2:
            log.warning("Speech rewrite failed: %s", exc2)
    translated = _plain_for_speech(translated)
    if translated and translated != spoken:
        _cache_set(cache_key, {"text": translated})
    return translated or spoken


def speak(text: str, language_id: str) -> dict[str, Any]:
    if not sarvam_client.enabled:
        raise SarvamError("Sarvam is not configured", status=503)
    language = resolve_language(language_id)
    spoken = _for_speech(text, language)
    audio = sarvam_client.speak(spoken, language_code=language["bcp47"])
    return {
        **audio,
        "language": language,
        "provider": "sarvam",
    }


def localize_news(title: str, body: str, language_id: str) -> dict[str, Any]:
    language = resolve_language(language_id)
    title = (title or "").strip()
    body = (body or "").strip()
    cache_key = f"news:{language['id']}:{hash(title + body[:180])}"
    cached = _cache_get(cache_key)
    if cached:
        return {**cached, "cached": True}
    if language["id"] == "en":
        result = {
            "title": title,
            "summary": body,
            "language": language,
            "provider": "passthrough",
        }
        _cache_set(cache_key, result)
        return result
    blob = f"TITLE: {title}\nSUMMARY: {body}"
    rewritten = _rewrite(
        f"Translate this crypto news card into {language['english']}. "
        "Keep TITLE: and SUMMARY: labels in English. Keep tickers and numbers.\n\n"
        f"{blob}",
        language,
    )
    loc_title = title
    loc_summary = body
    parts = re.split(r"(?i)\b(TITLE|SUMMARY)\s*:\s*", rewritten)
    if len(parts) >= 3:
        mapped: dict[str, str] = {}
        i = 1
        while i + 1 < len(parts):
            mapped[parts[i].upper()] = parts[i + 1].strip()
            i += 2
        loc_title = mapped.get("TITLE") or loc_title
        loc_summary = mapped.get("SUMMARY") or loc_summary
    result = {
        "title": loc_title,
        "summary": loc_summary,
        "language": language,
        "provider": "sarvam" if sarvam_client.enabled else "fallback",
    }
    _cache_set(cache_key, result)
    return result


def _plain_for_speech(text: str) -> str:
    cleaned = re.sub(r"[#*_`>]+", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:2400]


def _decode_audio(audio_b64: str) -> bytes:
    payload = (audio_b64 or "").strip()
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload)
    except Exception as exc:
        raise ValueError("Invalid audio") from exc
    if not raw:
        raise ValueError("Empty audio")
    if len(raw) > 6 * 1024 * 1024:
        raise ValueError("Audio too large")
    return raw


def _persist_ask(
    user_id: str,
    thread_id: str | None,
    question: str,
    reply: str,
    coin_id: str | None,
) -> str | None:
    try:
        from bson import ObjectId
    except Exception:
        return None
    try:
        now = _now()
        tid = thread_id
        if tid:
            try:
                oid = ObjectId(tid)
            except Exception:
                oid = None
            doc = (
                db.ai_threads.find_one({"_id": oid, "user_id": user_id})
                if oid
                else None
            )
            if not doc:
                tid = None
        if not tid:
            inserted = db.ai_threads.insert_one(
                {
                    "user_id": user_id,
                    "title": question[:72],
                    "coin_id": coin_id,
                    "source": "bhasha",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            tid = str(inserted.inserted_id)
        else:
            db.ai_threads.update_one(
                {"_id": ObjectId(tid), "user_id": user_id},
                {"$set": {"updated_at": now}},
            )
        db.ai_messages.insert_many(
            [
                {
                    "thread_id": tid,
                    "role": "user",
                    "content": question,
                    "created_at": now,
                },
                {
                    "thread_id": tid,
                    "role": "assistant",
                    "content": reply,
                    "created_at": now,
                },
            ]
        )
        return tid
    except Exception as exc:
        log.warning("Bhasha persist skipped: %s", exc)
        return None
