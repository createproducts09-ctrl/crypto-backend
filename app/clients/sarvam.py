from __future__ import annotations

import inspect
import logging
import os
from typing import Any

from sarvamai import SarvamAI

log = logging.getLogger(__name__)


class SarvamError(RuntimeError):
    def __init__(self, message: str, *, status: int = 502):
        super().__init__(message)
        self.status = status


class SarvamClient:
    """Official sarvamai SDK wrapper — STT, TTS, translate, chat."""

    def __init__(self) -> None:
        self._sdk: SarvamAI | None = None
        self._key = ""
        self._refresh()

    def _refresh(self) -> None:
        from dotenv import load_dotenv

        load_dotenv(override=True)
        from app.config import Config

        self.api_key = (
            os.getenv("SARVAM_API_KEY") or getattr(Config, "SARVAM_API_KEY", "") or ""
        ).strip()
        self.base_url = (
            os.getenv("SARVAM_BASE_URL")
            or getattr(Config, "SARVAM_BASE_URL", "")
            or "https://api.sarvam.ai"
        ).rstrip("/")
        self.chat_model = (
            os.getenv("SARVAM_CHAT_MODEL")
            or getattr(Config, "SARVAM_CHAT_MODEL", "")
            or "sarvam-105b-conversations"
        ).strip()
        if self.chat_model in {"", "sarvam-m", "sarvam-m-24b"}:
            self.chat_model = "sarvam-105b-conversations"
        self.tts_speaker = (
            os.getenv("SARVAM_TTS_SPEAKER")
            or getattr(Config, "SARVAM_TTS_SPEAKER", "")
            or "shubh"
        ).strip()
        if self.api_key and (self._sdk is None or self._key != self.api_key):
            self._sdk = SarvamAI(api_subscription_key=self.api_key)
            self._key = self.api_key

    @property
    def enabled(self) -> bool:
        self._refresh()
        return bool(self.api_key and self._sdk)

    def _client(self) -> SarvamAI:
        self._refresh()
        if not self._sdk:
            raise SarvamError("Sarvam is not configured", status=503)
        return self._sdk

    def _wrap(self, action: str, exc: Exception) -> SarvamError:
        detail = str(exc).strip() or exc.__class__.__name__
        return SarvamError(f"Sarvam {action} failed: {detail[:400]}", status=502)

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "audio.webm",
        mime: str = "audio/webm",
        language_code: str | None = None,
        mode: str = "transcribe",
    ) -> dict[str, Any]:
        client = self._client()
        codec = _codec_from_mime(mime)
        kwargs: dict[str, Any] = {
            "file": (filename, audio, mime or "application/octet-stream"),
            "model": "saaras:v3",
            "mode": mode or "transcribe",
        }
        if language_code and language_code != "unknown":
            kwargs["language_code"] = language_code
        if codec:
            kwargs["input_audio_codec"] = codec
        try:
            response = client.speech_to_text.transcribe(**kwargs)
        except Exception as exc:
            raise self._wrap("STT", exc) from exc
        return {
            "transcript": (getattr(response, "transcript", None) or "").strip(),
            "language_code": getattr(response, "language_code", None),
        }

    def speak(
        self,
        text: str,
        *,
        language_code: str = "hi-IN",
        speaker: str | None = None,
    ) -> dict[str, str]:
        client = self._client()
        clipped = (text or "").strip()
        if not clipped:
            raise SarvamError("Nothing to speak", status=400)
        if len(clipped) > 2400:
            clipped = clipped[:2400].rsplit(" ", 1)[0]
        try:
            convert = client.text_to_speech.convert
            params = inspect.signature(convert).parameters
            kwargs: dict[str, Any] = {
                "model": "bulbul:v3",
                "text": clipped,
                "speaker": (speaker or self.tts_speaker or "shubh").lower(),
            }
            if "target_language_code" in params:
                kwargs["target_language_code"] = language_code
            else:
                kwargs["language_code"] = language_code
            if "pace" in params:
                kwargs["pace"] = 1.0
            if "output_audio_codec" in params:
                kwargs["output_audio_codec"] = "mp3"
            response = convert(**kwargs)
        except Exception as exc:
            raise self._wrap("TTS", exc) from exc
        audios = list(getattr(response, "audios", None) or [])
        if not audios:
            raise SarvamError("Sarvam TTS returned no audio", status=502)
        return {"audio_base64": audios[0], "mime": "audio/mpeg"}

    def translate(
        self,
        text: str,
        *,
        source_language_code: str = "en-IN",
        target_language_code: str = "hi-IN",
    ) -> str:
        client = self._client()
        source = (text or "").strip()
        if not source:
            return ""
        if source_language_code == target_language_code:
            return source
        out: list[str] = []
        for chunk in _chunk_text(source, 900):
            try:
                response = client.text.translate(
                    input=chunk,
                    source_language_code=source_language_code,
                    target_language_code=target_language_code,
                    mode="formal",
                    model="mayura:v1",
                )
            except Exception as exc:
                raise self._wrap("translate", exc) from exc
            piece = (getattr(response, "translated_text", None) or "").strip()
            if piece:
                out.append(piece)
        return "\n\n".join(out).strip() or source

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> str:
        client = self._client()
        models = []
        for name in (self.chat_model, "sarvam-105b-conversations", "sarvam-105b"):
            if name and name not in models:
                models.append(name)
        payload = [
            {"role": m.get("role") or "user", "content": m.get("content") or ""}
            for m in messages
            if (m.get("content") or "").strip()
        ]
        last_error: Exception | None = None
        for model in models:
            try:
                response = client.chat.completions(
                    model=model,
                    messages=payload,
                    temperature=temperature,
                    top_p=1,
                    max_tokens=max_tokens,
                )
                text = _sdk_message_text(response)
                if text:
                    return text
                last_error = SarvamError(
                    f"Sarvam chat returned empty text (model={model})",
                    status=502,
                )
            except Exception as exc:
                last_error = exc
                log.warning("Sarvam SDK chat %s failed: %s", model, exc)
                try:
                    text = self._chat_rest(
                        payload,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    if text:
                        return text
                except Exception as rest_exc:
                    last_error = rest_exc
                    log.warning("Sarvam REST chat %s failed: %s", model, rest_exc)
        raise self._wrap("chat", last_error or RuntimeError("Sarvam chat failed"))

    def _chat_rest(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        import httpx

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": 1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        with httpx.Client(timeout=120.0) as http:
            resp = http.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "api-subscription-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 400:
            raise SarvamError(
                f"Sarvam chat failed ({resp.status_code}): {(resp.text or '')[:240]}",
                status=502 if resp.status_code >= 500 else resp.status_code,
            )
        return _sdk_message_text(resp.json())


def _sdk_message_text(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices") or []
    else:
        choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    first = choices[0]
    message = (
        first.get("message")
        if isinstance(first, dict)
        else getattr(first, "message", None)
    )
    if message is None:
        content = None
        reasoning = None
    elif isinstance(message, dict):
        content = message.get("content")
        reasoning = message.get("reasoning_content")
    else:
        content = getattr(message, "content", None)
        reasoning = getattr(message, "reasoning_content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(getattr(item, "text", None) or item or ""))
        content = "".join(parts)
    return str(content or reasoning or "").strip()


def _codec_from_mime(mime: str) -> str | None:
    raw = (mime or "").lower()
    if "webm" in raw:
        return "webm"
    if "mpeg" in raw or raw.endswith("mp3"):
        return "mp3"
    if "wav" in raw:
        return "wav"
    if "mp4" in raw or "m4a" in raw:
        return "mp4"
    if "ogg" in raw:
        return "ogg"
    return None


def _chunk_text(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for para in text.split("\n"):
        piece = para.strip()
        if not piece:
            continue
        if size + len(piece) + 1 > limit and buf:
            parts.append("\n".join(buf))
            buf = [piece]
            size = len(piece)
        else:
            buf.append(piece)
            size += len(piece) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts or [text[:limit]]


sarvam_client = SarvamClient()
