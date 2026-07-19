from __future__ import annotations

import logging
from typing import Any, Optional

from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
from pymongo import MongoClient
from pymongo.database import Database

logger = logging.getLogger(__name__)

jwt = JWTManager()
socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")
mongo_client: Optional[MongoClient] = None


class _DBProxy:
    """Lazy proxy so `from app.extensions import db` stays valid after init."""

    _db: Optional[Database] = None

    def bind(self, database: Database) -> None:
        self._db = database

    def __getattr__(self, item: str) -> Any:
        if self._db is None:
            raise RuntimeError("MongoDB is not initialized")
        return getattr(self._db, item)


db = _DBProxy()


def init_mongo(uri: str):
    global mongo_client
    try:
        mongo_client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,  # 5 second timeout for server selection
            connectTimeoutMS=5000,  # 5 second timeout for initial connection
            socketTimeoutMS=5000,  # 5 second timeout for socket operations
        )
        # Test connection
        mongo_client.admin.command('ping')
        logger.info("MongoDB connection successful")
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        raise
    try:
        database = mongo_client.get_default_database()
    except Exception:
        database = None
    if database is None:
        database = mongo_client["crypto_research"]
    db.bind(database)
    return db
