"""Shared news data models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class NewsRecord:
    provider: str
    provider_news_id: str
    published_at_utc: datetime
    fetched_at_utc: datetime
    first_seen_at_utc: datetime
    available_at_utc: datetime
    tickers: list[str]
    company_names: list[str]
    headline: str
    summary: str
    source: str
    url: str
    content_hash: str
    raw_path: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "published_at_utc",
            "fetched_at_utc",
            "first_seen_at_utc",
            "available_at_utc",
        ):
            data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsRecord":
        converted = dict(data)
        for key in (
            "published_at_utc",
            "fetched_at_utc",
            "first_seen_at_utc",
            "available_at_utc",
        ):
            converted[key] = datetime.fromisoformat(
                str(converted[key]).replace("Z", "+00:00")
            )
        return cls(**converted)


@dataclass
class TickerNewsScore:
    ticker: str
    sentiment: float
    count: int
    latest_available_at_utc: datetime | None


def stable_content_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
