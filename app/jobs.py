from __future__ import annotations

from app.clients.coingecko import coingecko
from app.services.alert_service import evaluate_all_alerts
from app.services.coin_service import upsert_markets
from app.services.conviction_service import score_convictions
from app.services.duel_service import resolve_duels
from app.services.news_service import sync_news_feed
from app.services.quiet_service import cleanup_ready_expired


def sync_markets() -> dict:
    # One page only to stay under CoinGecko free-tier limits
    markets = coingecko.markets(page=1, per_page=100)
    total = upsert_markets(markets)
    return {"synced": total}


def sync_news() -> dict:
    return {"synced": sync_news_feed()}


def evaluate_alerts() -> dict:
    return {"triggered": evaluate_all_alerts()}


def run_conviction_scoring() -> dict:
    return score_convictions()


def run_duel_resolution() -> dict:
    return resolve_duels()


def run_quiet_cleanup() -> dict:
    return cleanup_ready_expired()
