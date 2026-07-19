from __future__ import annotations

import hashlib
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.clients.ai import ai_service
from app.clients.cryptocompare import cryptocompare
from app.extensions import db

RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]


def _sentiment(title: str, body: str) -> str:
    lower = f"{title} {body}".lower()
    if any(w in lower for w in ["surge", "rally", "record", "bullish", "approval", "etf inflow"]):
        return "bullish"
    if any(w in lower for w in ["hack", "crash", "ban", "bearish", "lawsuit", "exploit", "sec charges"]):
        return "bearish"
    return "neutral"


def _parse_rss(xml_text: str, source_fallback: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    channel = root.find("channel")
    source = source_fallback
    if channel is not None and channel.findtext("title"):
        source = channel.findtext("title") or source_fallback
    for item in root.findall(".//item")[:25]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        published_at = datetime.now(timezone.utc)
        if pub:
            try:
                published_at = parsedate_to_datetime(pub)
            except Exception:
                pass
        if not title:
            continue
        external_id = hashlib.sha1((link or title).encode()).hexdigest()[:16]
        items.append(
            {
                "external_id": external_id,
                "title": title,
                "body": description[:3000],
                "url": link,
                "source": source,
                "image": None,
                "categories": ["News"],
                "published_at": published_at,
                "sentiment": _sentiment(title, description),
                "market_impact": "medium",
            }
        )
    return items


def _fetch_rss() -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for feed in RSS_FEEDS:
            try:
                resp = client.get(feed, headers={"User-Agent": "LumenKeelResearch/1.0"})
                resp.raise_for_status()
                host = urlparse(feed).hostname or "rss"
                collected.extend(_parse_rss(resp.text, host))
            except Exception:
                continue
    return collected


def sync_news_feed(limit: int = 40) -> int:
    raw_items: list[dict[str, Any]] = []
    try:
        cc = cryptocompare.latest_news(limit=limit)
        for item in cc:
            external_id = str(item.get("id") or item.get("guid") or item.get("published_on"))
            title = item.get("title") or ""
            body = item.get("body") or ""
            categories = (item.get("categories") or "").split("|") if item.get("categories") else []
            sentiment = _sentiment(title, body)
            raw_items.append(
                {
                    "external_id": external_id,
                    "title": title,
                    "body": body[:3000],
                    "url": item.get("url") or item.get("guid"),
                    "source": (item.get("source_info") or {}).get("name") or item.get("source"),
                    "image": item.get("imageurl"),
                    "categories": [c for c in categories if c],
                    "published_at": datetime.fromtimestamp(item.get("published_on") or 0, tz=timezone.utc)
                    if item.get("published_on")
                    else datetime.now(timezone.utc),
                    "sentiment": sentiment,
                    "market_impact": "high" if sentiment != "neutral" else "medium",
                }
            )
    except Exception:
        raw_items = []

    if not raw_items:
        raw_items = _fetch_rss()

    count = 0
    for item in raw_items[:limit]:
        doc = {
            **item,
            "ai_summary": ai_service.summarize_news(item.get("title") or "", item.get("body") or ""),
            "updated_at": datetime.now(timezone.utc),
        }
        db.news.update_one({"external_id": doc["external_id"]}, {"$set": doc}, upsert=True)
        count += 1
    return count


def _sync_news_async():
    """Sync news feed in background."""
    try:
        sync_news_feed()
    except Exception:
        pass


def list_news(category: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if category:
        query["categories"] = category
    if db.news.count_documents({}) == 0:
        # Sync in background, return empty for now
        thread = threading.Thread(target=_sync_news_async, daemon=True)
        thread.start()
        return []
    cursor = db.news.find(query, {"_id": 0}).sort("published_at", -1).limit(limit)
    out = []
    for n in cursor:
        if n.get("published_at"):
            n["published_at"] = n["published_at"].isoformat()
        out.append(n)
    return out


def get_article(external_id: str) -> dict | None:
    article = db.news.find_one({"external_id": external_id}, {"_id": 0})
    if article and article.get("published_at"):
        article["published_at"] = article["published_at"].isoformat()
    return article
