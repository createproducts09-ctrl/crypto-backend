import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)

    MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/crypto_research")

    COINGECKO_BASE_URL = os.getenv("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
    CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    # Comma-separated fallbacks tried when the primary model is quota-blocked / missing.
    GEMINI_MODEL_FALLBACKS = [
        m.strip()
        for m in os.getenv(
            "GEMINI_MODEL_FALLBACKS",
            "gemini-flash-latest,gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash",
        ).split(",")
        if m.strip()
    ]

    # Free OpenAI-compatible fallback when Gemini quota is exhausted.
    # Get a key at https://console.groq.com/keys
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM = os.getenv("RESEND_FROM", "Lumen Keel <onboarding@resend.dev>")

    # Comma-separated origins, or "*" for all.
    # Include common Next.js ports — `next dev` often jumps to 3001/3002 when 3000 is busy.
    _cors = os.getenv(
        "CORS_ORIGINS",
        ",".join(
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in range(3000, 3011)
        ),
    ).strip()
    if not _cors or _cors == "*":
        CORS_ORIGINS = "*"
    else:
        _parts = [o.strip() for o in _cors.split(",") if o.strip()]
        CORS_ORIGINS = "*" if "*" in _parts else _parts
    PORT = int(os.getenv("PORT", "5001"))
