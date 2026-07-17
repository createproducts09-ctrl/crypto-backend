from __future__ import annotations

import logging
from typing import Any

import httpx
from flask import current_app

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Lumen Keel <onboarding@resend.dev>"


def _api_key() -> str:
    try:
        return (current_app.config.get("RESEND_API_KEY") or "").strip()
    except RuntimeError:
        import os

        return (os.getenv("RESEND_API_KEY") or "").strip()


def _from_address() -> str:
    try:
        return (current_app.config.get("RESEND_FROM") or DEFAULT_FROM).strip()
    except RuntimeError:
        import os

        return (os.getenv("RESEND_FROM") or DEFAULT_FROM).strip()


def send_email(*, to: str, subject: str, html: str, text: str | None = None) -> dict[str, Any]:
    """Send an email via Resend. Returns {ok, id?} or {ok: False, error}."""
    key = _api_key()
    if not key:
        logger.warning("RESEND_API_KEY missing — email to %s skipped", to)
        return {"ok": False, "error": "resend_not_configured", "skipped": True}

    payload: dict[str, Any] = {
        "from": _from_address(),
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text

    try:
        with httpx.Client(timeout=20.0) as client:
            res = client.post(
                RESEND_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        data = res.json() if res.content else {}
        if res.status_code >= 400:
            err = data.get("message") or data.get("error") or res.text
            logger.warning("Resend failed (%s): %s", res.status_code, err)
            return {"ok": False, "error": str(err), "status": res.status_code}
        return {"ok": True, "id": data.get("id")}
    except Exception as exc:
        logger.warning("Resend request error: %s", exc)
        return {"ok": False, "error": str(exc)}


def send_verification_email(to: str, username: str, code: str) -> dict[str, Any]:
    subject = "Verify your Lumen Keel email"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; max-width: 520px; margin: 0 auto; color: #0f172a;">
      <h1 style="font-size: 22px; margin-bottom: 8px;">Confirm your email</h1>
      <p style="color:#475569; line-height:1.5;">Hi {username},</p>
      <p style="color:#475569; line-height:1.5;">
        Welcome to Lumen Keel. Enter this code in the app to verify your email:
      </p>
      <p style="font-size: 32px; letter-spacing: 8px; font-weight: 700; margin: 24px 0; color:#0E7C74;">
        {code}
      </p>
      <p style="color:#64748b; font-size: 13px;">This code expires in 24 hours. If you didn’t create a Lumen Keel account, you can ignore this email.</p>
    </div>
    """
    text = f"Hi {username},\n\nYour Lumen Keel verification code is: {code}\nIt expires in 24 hours.\n"
    return send_email(to=to, subject=subject, html=html, text=text)


def send_welcome_email(to: str, username: str) -> dict[str, Any]:
    subject = "Thank you for downloading Lumen Keel"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; max-width: 520px; margin: 0 auto; color: #0f172a;">
      <h1 style="font-size: 22px; margin-bottom: 8px;">We’re glad you’re here</h1>
      <p style="color:#475569; line-height:1.5;">Hi {username},</p>
      <p style="color:#475569; line-height:1.5;">
        Thank you for downloading Lumen Keel. We’re grateful you chose us to explore crypto research —
        swipe discoveries, build conviction, and stay clear-headed with Quiet Mode.
      </p>
      <p style="color:#475569; line-height:1.5;">
        If you ever need help, just reply to this note. We’re building this with researchers like you in mind.
      </p>
      <p style="color:#0E7C74; font-weight: 600; margin-top: 28px;">— The Lumen Keel team</p>
    </div>
    """
    text = (
        f"Hi {username},\n\n"
        "Thank you for downloading Lumen Keel. We’re grateful you chose us for crypto research.\n\n"
        "— The Lumen Keel team\n"
    )
    return send_email(to=to, subject=subject, html=html, text=text)
