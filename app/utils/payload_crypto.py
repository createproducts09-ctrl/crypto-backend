from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALG = "AES-256-GCM"
ENC_FLAG = 1


def _key_bytes() -> bytes | None:
    raw = (os.getenv("API_PAYLOAD_KEY") or "").strip()
    if not raw:
        return None
    # Accept hex (64 chars) or any passphrase (SHA-256 derived)
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encryption_enabled() -> bool:
    flag = (os.getenv("API_PAYLOAD_ENCRYPTION") or "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return _key_bytes() is not None


def encrypt_payload(data: Any) -> dict[str, Any]:
    key = _key_bytes()
    if not key:
        raise RuntimeError("API_PAYLOAD_KEY is not configured")
    plaintext = json.dumps(data, default=str, separators=(",", ":")).encode("utf-8")
    iv = os.urandom(12)
    aes = AESGCM(key)
    sealed = aes.encrypt(iv, plaintext, None)  # ciphertext || tag(16)
    ct, tag = sealed[:-16], sealed[-16:]
    return {
        "enc": ENC_FLAG,
        "alg": ALG,
        "iv": base64.b64encode(iv).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }


def decrypt_payload(envelope: dict[str, Any]) -> Any:
    key = _key_bytes()
    if not key:
        raise RuntimeError("API_PAYLOAD_KEY is not configured")
    if not isinstance(envelope, dict) or envelope.get("enc") != ENC_FLAG:
        raise ValueError("Not an encrypted payload")
    iv = base64.b64decode(envelope["iv"])
    tag = base64.b64decode(envelope["tag"])
    ct = base64.b64decode(envelope["ct"])
    aes = AESGCM(key)
    plaintext = aes.decrypt(iv, ct + tag, None)
    return json.loads(plaintext.decode("utf-8"))


def is_encrypted_envelope(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and data.get("enc") == ENC_FLAG
        and data.get("alg") == ALG
        and isinstance(data.get("iv"), str)
        and isinstance(data.get("ct"), str)
        and isinstance(data.get("tag"), str)
    )
