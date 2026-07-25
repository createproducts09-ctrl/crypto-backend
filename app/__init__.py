from __future__ import annotations

import os
from typing import Optional

from flask import Flask, make_response, request
from flask_cors import CORS

from app.bootstrap import ensure_indexes, seed_demo_community
from app.config import Config
from app.extensions import init_mongo, jwt, socketio
from app.scheduler import start_background_jobs


def _origin_allowed(origin: Optional[str], allowed) -> bool:
    if not origin:
        return False
    if allowed == "*":
        return True
    if isinstance(allowed, (list, tuple, set)):
        return origin in allowed
    return origin == allowed


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    origins = app.config["CORS_ORIGINS"]
    # flask-cors + SocketIO/Werkzeug can skip ACAO on OPTIONS; also set headers manually below.
    CORS(
        app,
        resources={r"/*": {"origins": origins}},
        supports_credentials=False if origins == "*" else True,
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
        expose_headers=["Content-Type"],
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=86400,
    )

    @app.before_request
    def _cors_preflight():
        if request.method != "OPTIONS":
            return None
        resp = make_response("", 204)
        origin = request.headers.get("Origin")
        if _origin_allowed(origin, origins):
            resp.headers["Access-Control-Allow-Origin"] = (
                "*" if origins == "*" else origin
            )
            if origins != "*":
                resp.headers["Vary"] = "Origin"
                resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = (
            request.headers.get("Access-Control-Request-Headers")
            or "Authorization, Content-Type, X-Requested-With"
        )
        resp.headers["Access-Control-Allow-Methods"] = (
            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.after_request
    def _cors_headers(response):
        origin = request.headers.get("Origin")
        if _origin_allowed(origin, origins):
            response.headers["Access-Control-Allow-Origin"] = (
                "*" if origins == "*" else origin
            )
            if origins != "*":
                response.headers["Vary"] = "Origin"
                response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers.setdefault(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-Requested-With",
            )
            response.headers.setdefault(
                "Access-Control-Allow-Methods",
                "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS",
            )
        return response

    jwt.init_app(app)
    init_mongo(app.config["MONGODB_URI"])
    socketio.init_app(app, cors_allowed_origins="*" if origins == "*" else origins)

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
    from app.blueprints.billing import bp as billing_bp
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
    app.register_blueprint(billing_bp, url_prefix="/api/billing")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "crypto-research-api"}

    # Under Flask debug reloader, only start jobs in the child process
    should_start = os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug
    if should_start and not getattr(app, "_lumenkeel_jobs_started", False):
        start_background_jobs(app)
        app._lumenkeel_jobs_started = True

    return app
