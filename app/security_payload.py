from __future__ import annotations

import logging

from flask import Flask, Response, jsonify, request

from app.utils.payload_crypto import (
    decrypt_payload,
    encrypt_payload,
    encryption_enabled,
    is_encrypted_envelope,
)

log = logging.getLogger(__name__)

SKIP_PREFIXES = (
    "/api/health",
    "/socket.io",
)


def _should_skip_path(path: str) -> bool:
    if not path:
        return True
    for prefix in SKIP_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def register_payload_encryption(app: Flask) -> None:
    """Decrypt encrypted JSON requests; encrypt JSON responses (AES-256-GCM)."""

    @app.before_request
    def _decrypt_request():
        if not encryption_enabled():
            return None
        if request.method == "OPTIONS":
            return None
        if _should_skip_path(request.path):
            return None
        if not request.is_json:
            return None
        body = request.get_json(silent=True)
        if not is_encrypted_envelope(body):
            # When encryption is on, require encrypted bodies for mutating methods
            # with a JSON content-type (except empty).
            if request.method in {"POST", "PUT", "PATCH"} and body is not None:
                # Allow plaintext only if client did not opt into encryption yet —
                # once X-Accept-Encrypted is sent, require encrypted bodies.
                if request.headers.get("X-Accept-Encrypted") == "1":
                    return jsonify(
                        {
                            "error": "Encrypted request body required",
                            "code": "encryption_required",
                        }
                    ), 400
            return None
        try:
            plain = decrypt_payload(body)
        except Exception as exc:
            log.warning("Request decrypt failed: %s", exc)
            return jsonify(
                {"error": "Could not decrypt request", "code": "decrypt_failed"}
            ), 400
        # Flask caches by silent bool index: (silent=False, silent=True)
        request._cached_json = (plain, plain)  # type: ignore[attr-defined]
        return None

    @app.after_request
    def _encrypt_response(response: Response):
        if not encryption_enabled():
            return response
        if request.method == "OPTIONS":
            return response
        if _should_skip_path(request.path):
            return response
        # Only encrypt when client can decrypt
        if request.headers.get("X-Accept-Encrypted") != "1":
            return response
        ctype = (response.mimetype or "").lower()
        if "json" not in ctype:
            return response
        if response.status_code == 204 or not response.get_data():
            return response
        try:
            data = response.get_json(silent=True)
        except Exception:
            data = None
        if data is None:
            return response
        if is_encrypted_envelope(data):
            return response
        try:
            envelope = encrypt_payload(data)
        except Exception as exc:
            log.warning("Response encrypt failed: %s", exc)
            return response
        response.set_data(app.json.dumps(envelope))
        response.headers["Content-Type"] = "application/json"
        response.headers["X-Payload-Encrypted"] = "1"
        response.headers["Cache-Control"] = "no-store"
        return response
