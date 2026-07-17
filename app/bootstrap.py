from datetime import datetime, timezone

from app.extensions import db


def ensure_indexes():
    db.users.create_index("email", unique=True)
    db.users.create_index("username", unique=True)
    db.users.create_index("email_verification_code")
    db.coins.create_index("id", unique=True)
    db.coins.create_index("market_cap_rank")
    db.watchlist.create_index([("user_id", 1), ("coin_id", 1)], unique=True)
    db.portfolio.create_index([("user_id", 1), ("coin_id", 1)], unique=True)
    db.baskets.create_index([("user_id", 1), ("updated_at", -1)])
    db.discover_swipes.create_index([("user_id", 1), ("coin_id", 1)], unique=True)
    db.discover_swipes.create_index([("user_id", 1), ("action", 1), ("created_at", 1)])
    db.discover_swipes.create_index([("user_id", 1), ("day_key", 1)])
    db.news.create_index("external_id", unique=True)
    db.posts.create_index([("section", 1), ("created_at", -1)])
    db.convictions.create_index([("user_id", 1), ("entry_at", -1)])
    db.convictions.create_index([("user_id", 1), ("coin_id", 1), ("status", 1)])
    db.quiet_prefs.create_index("user_id", unique=True)
    db.cooling_bag.create_index([("user_id", 1), ("coin_id", 1)], unique=True)
    db.cooling_bag.create_index([("ready_at", 1)])
    db.duel_entries.create_index([("coin_id", 1), ("horizon", 1), ("side", 1), ("status", 1), ("created_at", 1)])
    db.duel_entries.create_index([("user_id", 1), ("created_at", -1)])
    db.duel_entries.create_index([("user_id", 1), ("coin_id", 1), ("horizon", 1), ("status", 1)])
    db.duels.create_index([("resolve_at", 1), ("status", 1)])
    db.duels.create_index([("status", 1), ("created_at", -1)])


def seed_demo_community():
    if db.posts.count_documents({}) > 0:
        return
    samples = [
        {
            "user_id": "system",
            "username": "lumenkeel_research",
            "section": "market_analysis",
            "title": "How to read liquidity before chasing a breakout",
            "body": "Volume confirmation matters more than the candle. Look for rising spot volume, stable spreads, and whether derivatives funding is stretched.",
        },
        {
            "user_id": "system",
            "username": "lumenkeel_research",
            "section": "bitcoin",
            "title": "A calm framework for BTC macro weeks",
            "body": "Separate narrative noise from measurable flows: ETF prints, realized volatility, and whether price is reclaiming prior range highs with breadth.",
        },
        {
            "user_id": "system",
            "username": "lumenkeel_research",
            "section": "ai",
            "title": "Using AI summaries without outsourcing judgment",
            "body": "Treat model output as a structured brief. Verify claims against tokenomics, unlock calendars, and on-chain usage before sizing any idea.",
        },
    ]
    now = datetime.now(timezone.utc)
    for s in samples:
        db.posts.insert_one(
            {
                **s,
                "upvotes": 12,
                "downvotes": 1,
                "score": 11,
                "comment_count": 0,
                "bookmarks": [],
                "followers": [],
                "created_at": now,
            }
        )
