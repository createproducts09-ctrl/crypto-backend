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
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM = os.getenv("RESEND_FROM", "Lumen Keel <onboarding@resend.dev>")

    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
    PORT = int(os.getenv("PORT", "5001"))
