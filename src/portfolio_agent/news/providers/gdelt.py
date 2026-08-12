"""GDELT 2.0 provider - reference implementation.

Queries the free GDELT DOC 2.0 article API for articles mentioning a company and maps
them to NewsRecords. GDELT gives broad web coverage but weaker entity->ticker precision
than Finnhub or EDGAR, so the query is company-name based and each record is tagged with
a single ticker; callers should treat these as *candidate* mentions and validate mapping
precision on a labelled sample before turning this on for scoring (see survey #2).

NOTE: the GDELT API host was not reachable from the environment where this was written,
so this client is implemented to the documented API contract but has NOT been live-tested
here. Run `scripts/survey_news_sources.py --source gdelt` from a networked host to verify.

API: https://api.gdeltproject.org/api/v2/doc/doc
  query=<phrase>&mode=artlist&format=json&startdatetime=YYYYMMDDHHMMSS&enddatetime=...
GDELT article `seendate` (when GDELT first indexed the article) is the closest analogue
to a first-seen time and is used for available_at_utc.
"""

from __future__ import annotations

import json
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from portfolio_agent.news.models import NewsRecord, stable_content_hash

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def _get(url: str, params: dict[str, Any], user_agent: str, timeout_seconds: float,
         max_retries: int = 3, sleep=_time.sleep) -> Any:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": user_agent})
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == max_retries:
                raise
        except urllib.error.URLError:
            if attempt == max_retries:
                raise
        sleep(1.5 ** attempt)
    return {}


def _parse_seendate(value: str) -> datetime:
    # GDELT seendate format: YYYYMMDDTHHMMSSZ
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class GdeltNewsProvider:
    def __init__(
        self,
        user_agent: str = "portfolio-agent-evaluator contact@example.com",
        timeout_seconds: float = 30.0,
        max_records: int = 75,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_records = max_records

    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        params = {
            "query": f'"{company_name}"',
            "mode": "artlist",
            "format": "json",
            "maxrecords": self.max_records,
            "sort": "datedesc",
            "startdatetime": start_date.strftime("%Y%m%d") + "000000",
            "enddatetime": end_date.strftime("%Y%m%d") + "235959",
        }
        payload = _get(GDELT_DOC_URL, params, self.user_agent, self.timeout_seconds)
        articles = (payload or {}).get("articles", [])

        records: list[NewsRecord] = []
        for art in articles:
            seen = _parse_seendate(str(art.get("seendate", "")))
            url = str(art.get("url", ""))
            headline = str(art.get("title", "")).strip()
            source = str(art.get("domain", "gdelt"))
            payload_hash = {"provider": "gdelt", "url": url, "seendate": art.get("seendate")}
            records.append(NewsRecord(
                provider="gdelt",
                provider_news_id=stable_content_hash(payload_hash)[:24],
                published_at_utc=seen,
                fetched_at_utc=fetched_at_utc,
                first_seen_at_utc=seen,
                available_at_utc=seen,   # GDELT index time ~ first-seen
                tickers=[ticker.upper()],
                company_names=[company_name],
                headline=headline,
                summary="",
                source=source,
                url=url,
                content_hash=stable_content_hash(payload_hash),
                raw_path="",
            ))
        return records
