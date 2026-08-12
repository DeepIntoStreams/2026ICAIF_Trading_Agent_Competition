"""Finnhub company news provider.

The `company-news` endpoint returns at most ~250 items per (symbol, date-range) call
and has no pagination, so a wide range silently truncates. To get complete coverage
this client splits the requested range into <= `chunk_days` sub-windows, fetches each,
and deduplicates by Finnhub id. It also retries with exponential backoff on transient
HTTP 429 / 5xx responses.
"""

from __future__ import annotations

import json
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.news.normalizer import normalize_finnhub_company_news


FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


def _get_json(
    url: str,
    params: dict[str, Any],
    timeout_seconds: float,
    max_retries: int = 4,
    backoff_base: float = 1.5,
    sleep=_time.sleep,
) -> Any:
    full_url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        full_url,
        headers={"User-Agent": "portfolio-agent-evaluator/0.1"},
    )
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_err = exc
            # Retry only on rate-limit / server errors; re-raise auth/client errors.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == max_retries:
                raise
        except urllib.error.URLError as exc:
            last_err = exc
            if attempt == max_retries:
                raise
        sleep(backoff_base ** attempt)
    if last_err:
        raise last_err
    return []


def _chunk_ranges(start_date: date, end_date: date, chunk_days: int):
    cursor = start_date
    step = timedelta(days=max(1, chunk_days) - 1)
    while cursor <= end_date:
        window_end = min(cursor + step, end_date)
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


class FinnhubCompanyNewsProvider:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 30.0,
        chunk_days: int = 7,
        request_pause_seconds: float = 0.0,
    ):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        # Sub-window width; 7d keeps each call well under the ~250-item response cap
        # for the busiest tickers in this universe.
        self.chunk_days = chunk_days
        self.request_pause_seconds = request_pause_seconds

    def fetch_raw_company_news(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        seen_ids: set[str] = set()
        merged: list[dict[str, Any]] = []
        for win_start, win_end in _chunk_ranges(start_date, end_date, self.chunk_days):
            payload = _get_json(
                FINNHUB_COMPANY_NEWS_URL,
                {
                    "symbol": ticker,
                    "from": win_start.isoformat(),
                    "to": win_end.isoformat(),
                    "token": self.api_key,
                },
                self.timeout_seconds,
            )
            for item in payload or []:
                # Dedup across overlapping/adjacent windows by Finnhub id (fallback: url).
                key = str(item.get("id") or item.get("url") or id(item))
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                merged.append(item)
            if self.request_pause_seconds:
                _time.sleep(self.request_pause_seconds)
        return merged

    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        payload = self.fetch_raw_company_news(ticker, start_date, end_date)
        return normalize_finnhub_company_news(
            payloads=payload,
            ticker=ticker,
            company_name=company_name,
            fetched_at_utc=fetched_at_utc,
        )
