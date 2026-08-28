"""Automatic daily data fetch + publish for the web service.

This is what makes the service self-running: on each U.S. trading day it fetches fresh public
news from SEC EDGAR, builds the day's observation panel (point-in-time, no look-ahead), and
publishes it to the store the web app serves - with no human in the loop.

Two entry points:
  publish_day(date)      one-shot: fetch live news + build + publish that day's panel.
  run_scheduler(...)     background loop: fire publish_day on every NYSE trading day at the
                         configured time. Import and start it from app.py (LIVE_FETCH=1).

Prices: for the demo the shipped CSVs are the market feed. In production, ingest_market() is
the one seam to point at a real market-data API - the rest is unchanged.
"""

from __future__ import annotations

import sys
import threading
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
COMP = HERE.parent
ROOT = COMP.parent
sys.path.insert(0, str(COMP / "code"))
sys.path.insert(0, str(ROOT / "src"))

import ingest                                   # competition/code/ingest.py (live SEC fetch)
from panel import PanelBuilder                  # competition/code/panel.py
from portfolio_agent.nyse_calendar import is_trading_day, close_time_for

ET = ZoneInfo("America/New_York")


def publish_day(as_of: str, data_root: str, news_dir: str, out_dir: str,
                lookback_days: int = 45) -> dict:
    """Fetch live SEC news up to `as_of`, build the panel, publish it. Returns a summary."""
    start = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
    # 1) LIVE fetch of public news from SEC EDGAR into the news store
    n_news = ingest.ingest_news(data_root, start, as_of, news_dir)
    # 2) build the point-in-time panel from fresh news + the market feed, and publish it
    pb = PanelBuilder(data_root, news_dir)
    pb.publish(out_dir, as_of)                  # writes data/observations/<date>.json + market/daily
    panel = pb.build(as_of)
    return {"date": as_of, "news_fetched": n_news, "assets": len(panel["assets"]),
            "news_published": len(panel["news"]), "published_at": datetime.now(ET).isoformat()}


def run_scheduler(data_root: str, news_dir: str, out_dir: str,
                  publish_minutes_after_close: int = 15, poll_seconds: int = 60) -> threading.Thread:
    """Background thread: publish once per NYSE trading day, shortly after the close.

    Production-shaped: it wakes every `poll_seconds`, and when 'now' (ET) is a trading day past
    close + `publish_minutes_after_close`, it publishes that day (once). Swap the poll loop for
    a cron / APScheduler job in a real deployment if preferred - publish_day() is the payload.
    """
    published: set[str] = set()

    def loop():
        while True:
            now = datetime.now(ET)
            today = now.date()
            if is_trading_day(today) and today.isoformat() not in published:
                close = datetime.combine(today, close_time_for(today), tzinfo=ET)
                if now >= close + timedelta(minutes=publish_minutes_after_close):
                    try:
                        s = publish_day(today.isoformat(), data_root, news_dir, out_dir)
                        print(f"[autofetch] published {s['date']}: {s['news_published']} news items")
                        published.add(today.isoformat())
                    except Exception as exc:            # never let a fetch error kill the loop
                        print(f"[autofetch] error: {type(exc).__name__}: {exc}")
            _time.sleep(poll_seconds)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    # one-shot demo: fetch + publish a single day live
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--data-root", default="data/stock_data_1y")
    ap.add_argument("--news-dir", default="competition/web/live_news")
    ap.add_argument("--out", default="competition/web/live_store")
    a = ap.parse_args()
    print(publish_day(a.date, a.data_root, a.news_dir, a.out))
