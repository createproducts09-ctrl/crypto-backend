from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.clients.sarvam import SarvamError
from app.services import bhasha_service

bp = Blueprint("bhasha", __name__)


def _client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or request.remote_addr or "anon"


def _optional_user_id() -> str | None:
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:
        return None


def _rate(bucket: str, limit: int):
    if not bhasha_service.allow_request(_client_ip(), bucket, limit):
        return jsonify({"error": "Too many Bhasha requests. Try again shortly."}), 429
    return None


@bp.get("/status")
def status():
    return jsonify(bhasha_service.status())


@bp.get("/languages")
def languages():
    return jsonify({"languages": bhasha_service.LANGUAGES})


@bp.post("/brief")
def brief():
    limited = _rate("brief", 40)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    coin_id = (data.get("coin_id") or request.args.get("coin_id") or "bitcoin").strip()
    language = data.get("language") or request.args.get("language") or "hi"
    question = (data.get("question") or data.get("q") or "").strip() or None
    try:
        return jsonify(bhasha_service.brief(coin_id, language, question=question))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except SarvamError as exc:
        return jsonify({"error": str(exc), "code": "sarvam"}), exc.status
    except Exception as exc:
        return jsonify({"error": f"Bhasha brief unavailable: {exc}"}), 503


@bp.post("/ask")
def ask():
    limited = _rate("ask", 40)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or data.get("content") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    user_id = _optional_user_id()
    if user_id:
        try:
            from app.services import billing_service

            billing_service.assert_can_ai_chat(user_id)
        except PermissionError as exc:
            return jsonify({"error": str(exc), "code": "plan_limit", "upgrade": True}), 402
    try:
        return jsonify(
            bhasha_service.ask(
                question,
                data.get("language") or "hi",
                coin_id=(data.get("coin_id") or "").strip() or None,
                user_id=user_id,
                thread_id=(data.get("thread_id") or "").strip() or None,
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except SarvamError as exc:
        return jsonify({"error": str(exc), "code": "sarvam"}), exc.status
    except Exception as exc:
        return jsonify({"error": f"Bhasha ask unavailable: {exc}"}), 503


@bp.post("/assistant")
def assistant():
    limited = _rate("assistant", 50)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or data.get("content") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    history = data.get("history") or []
    if not isinstance(history, list):
        history = []
    try:
        return jsonify(bhasha_service.assistant(question, history))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except SarvamError as exc:
        return jsonify({"error": str(exc), "code": "sarvam"}), exc.status
    except Exception as exc:
        return jsonify({"error": f"Assistant unavailable: {exc}"}), 503


@bp.post("/stt")
def stt():
    limited = _rate("stt", 30)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    audio = data.get("audio") or data.get("audio_base64") or ""
    if not audio:
        return jsonify({"error": "audio required"}), 400
    try:
        return jsonify(
            bhasha_service.transcribe(
                audio,
                language_id=data.get("language"),
                mime=data.get("mime") or "audio/webm",
                filename=data.get("filename") or "clip.webm",
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except SarvamError as exc:
        return jsonify({"error": str(exc), "code": "sarvam"}), exc.status
    except Exception as exc:
        return jsonify({"error": f"Transcription unavailable: {exc}"}), 503


@bp.post("/tts")
def tts():
    limited = _rate("tts", 40)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    try:
        return jsonify(bhasha_service.speak(text, data.get("language") or "hi"))
    except SarvamError as exc:
        return jsonify({"error": str(exc), "code": "sarvam"}), exc.status
    except Exception as exc:
        return jsonify({"error": f"Speech unavailable: {exc}"}), 503


@bp.post("/news")
def news():
    limited = _rate("news", 60)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or data.get("body") or "").strip()
    if not title and not summary:
        return jsonify({"error": "title or summary required"}), 400
    try:
        return jsonify(
            bhasha_service.localize_news(title, summary, data.get("language") or "hi")
        )
    except SarvamError as exc:
        return jsonify({"error": str(exc), "code": "sarvam"}), exc.status
    except Exception as exc:
        return jsonify({"error": f"News localize unavailable: {exc}"}), 503
