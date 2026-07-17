from __future__ import annotations

import logging
import threading
import time

from flask import Flask

logger = logging.getLogger(__name__)


def _safe_run(label: str, fn) -> None:
    try:
        result = fn()
        logger.info("Background job %s completed: %s", label, result)
    except Exception as exc:
        logger.warning("Background job %s failed: %s", label, exc)


def start_background_jobs(app: Flask) -> None:
    """Lightweight in-process scheduler (no Redis/Celery)."""

    def runner():
        # Stagger first runs slightly after boot
        time.sleep(5)
        while True:
            with app.app_context():
                from app.jobs import (
                    evaluate_alerts,
                    run_conviction_scoring,
                    run_duel_resolution,
                    run_quiet_cleanup,
                    sync_markets,
                    sync_news,
                )

                _safe_run("sync_markets", sync_markets)
                time.sleep(2)
                _safe_run("sync_news", sync_news)
                time.sleep(2)
                _safe_run("evaluate_alerts", evaluate_alerts)
                time.sleep(2)
                _safe_run("score_convictions", run_conviction_scoring)
                time.sleep(1)
                _safe_run("resolve_duels", run_duel_resolution)
                time.sleep(1)
                _safe_run("quiet_cleanup", run_quiet_cleanup)
            time.sleep(180)

    thread = threading.Thread(target=runner, name="lumenkeel-jobs", daemon=True)
    thread.start()
