"""Provider-specific news normalization helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import NewsRecord, stable_content_hash


def _finnhub_published_at(payload: Mapping[str, Any]) -> datetime:
    raw_ts = payload.get("datetime")
    if raw_ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)


def normalize_finnhub_company_news(
    payloads: list[dict[str, Any]],
    ticker: str,
    company_name: str,
    fetched_at_utc: datetime,
    first_seen_lookup: Mapping[str, datetime] | None = None,
    raw_path: str | Path = "",
    historical_backfill_mode: bool = False,
) -> list[NewsRecord]:
    records: list[NewsRecord] = []
    first_seen_lookup = first_seen_lookup or {}

    for payload in payloads:
        provider_news_id = str(payload.get("id") or stable_content_hash(payload))
        published_at = _finnhub_published_at(payload)
        first_seen_at = first_seen_lookup.get(provider_news_id, fetched_at_utc)
        available_at = published_at if historical_backfill_mode else first_seen_at
        raw_for_hash = {
            "provider": "finnhub",
            "headline": payload.get("headline", ""),
            "summary": payload.get("summary", ""),
            "source": payload.get("source", ""),
            "url": payload.get("url", ""),
            "datetime": payload.get("datetime"),
        }
        records.append(
            NewsRecord(
                provider="finnhub",
                provider_news_id=provider_news_id,
                published_at_utc=published_at,
                fetched_at_utc=fetched_at_utc,
                first_seen_at_utc=first_seen_at,
                available_at_utc=available_at,
                tickers=[ticker],
                company_names=[company_name],
                headline=str(payload.get("headline", "")),
                summary=str(payload.get("summary", "")),
                source=str(payload.get("source", "")),
                url=str(payload.get("url", "")),
                content_hash=stable_content_hash(raw_for_hash),
                raw_path=str(raw_path),
            )
        )

    return records
