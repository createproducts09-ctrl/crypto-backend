import os

from flask import Flask
from flask_cors import CORS

from app.bootstrap import ensure_indexes, seed_demo_community
from app.config import Config
from app.extensions import init_mongo, jwt, socketio
from app.scheduler import start_background_jobs


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})
    jwt.init_app(app)
    init_mongo(app.config["MONGODB_URI"])
    socketio.init_app(app)

    try:
        ensure_indexes()
        seed_demo_community()
    except Exception as exc:
        app.logger.warning("Bootstrap skipped: %s", exc)

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.coins import bp as coins_bp
    from app.blueprints.discover import bp as discover_bp
    from app.blueprints.watchlist import bp as watchlist_bp
    from app.blueprints.portfolio import bp as portfolio_bp
    from app.blueprints.alerts import bp as alerts_bp
    from app.blueprints.community import bp as community_bp
    from app.blueprints.news import bp as news_bp
    from app.blueprints.ai import bp as ai_bp
    from app.blueprints.search import bp as search_bp
    from app.blueprints.users import bp as users_bp
    from app.blueprints.conviction import bp as conviction_bp
    from app.blueprints.quiet import bp as quiet_bp
    from app.blueprints.duels import bp as duels_bp
    from app import sockets  # noqa: F401

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(coins_bp, url_prefix="/api/coins")
    app.register_blueprint(discover_bp, url_prefix="/api/discover")
    app.register_blueprint(watchlist_bp, url_prefix="/api/watchlist")
    app.register_blueprint(portfolio_bp, url_prefix="/api/portfolio")
    app.register_blueprint(alerts_bp, url_prefix="/api/alerts")
    app.register_blueprint(community_bp, url_prefix="/api/community")
    app.register_blueprint(news_bp, url_prefix="/api/news")
    app.register_blueprint(ai_bp, url_prefix="/api/ai")
    app.register_blueprint(search_bp, url_prefix="/api/search")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(conviction_bp, url_prefix="/api/conviction")
    app.register_blueprint(quiet_bp, url_prefix="/api/quiet")
    app.register_blueprint(duels_bp, url_prefix="/api/duels")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "crypto-research-api"}

    # Under Flask debug reloader, only start jobs in the child process
    should_start = os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug
    if should_start and not getattr(app, "_lumenkeel_jobs_started", False):
        start_background_jobs(app)
        app._lumenkeel_jobs_started = True

    return app
