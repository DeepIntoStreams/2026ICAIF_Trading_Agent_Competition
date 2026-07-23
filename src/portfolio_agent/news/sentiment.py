"""Deterministic local news sentiment scoring."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from .models import NewsRecord, TickerNewsScore


POSITIVE_WORDS = {
    "beat",
    "beats",
    "raise",
    "raises",
    "upgrade",
    "growth",
    "profit",
    "record",
    "wins",
    "contract",
}

NEGATIVE_WORDS = {
    "miss",
    "cuts",
    "cut",
    "downgrade",
    "probe",
    "recall",
    "lawsuit",
    "loss",
    "falls",
    "warning",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]+", text.lower())


def _score_text(text: str) -> int:
    words = _words(text)
    positives = sum(1 for word in words if word in POSITIVE_WORDS)
    negatives = sum(1 for word in words if word in NEGATIVE_WORDS)
    return positives - negatives


def score_news_by_ticker(records: list[NewsRecord]) -> dict[str, TickerNewsScore]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    latest: dict[str, datetime] = {}

    for record in records:
        score = _score_text(f"{record.headline} {record.summary}")
        for ticker in record.tickers:
            totals[ticker] += score
            counts[ticker] += 1
            if (
                ticker not in latest
                or record.available_at_utc > latest[ticker]
            ):
                latest[ticker] = record.available_at_utc

    return {
        ticker: TickerNewsScore(
            ticker=ticker,
            sentiment=totals[ticker] / counts[ticker],
            count=counts[ticker],
            latest_available_at_utc=latest.get(ticker),
        )
        for ticker in counts
    }
