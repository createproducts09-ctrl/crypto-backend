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

    # Groq only — https://console.groq.com/keys
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "1"))
    GROQ_MAX_COMPLETION_TOKENS = int(os.getenv("GROQ_MAX_COMPLETION_TOKENS", "2048"))
    GROQ_TOP_P = float(os.getenv("GROQ_TOP_P", "1"))
    GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "medium")

    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM = os.getenv("RESEND_FROM", "Alphora Labs <onboarding@resend.dev>")

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
