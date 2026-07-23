"""GDELT supplement provider.

The MVP keeps GDELT behind a config flag because ticker mapping is less direct
than Finnhub company news.
"""

from __future__ import annotations

from datetime import date, datetime

from portfolio_agent.news.models import NewsRecord


class GdeltNewsProvider:
    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        return []
