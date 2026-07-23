"""SEC EDGAR supplement provider.

This MVP provider exposes the same interface as live news providers. It returns
no records unless a later task supplies CIK mapping and filing normalization.
"""

from __future__ import annotations

from datetime import date, datetime

from portfolio_agent.news.models import NewsRecord


class SecEdgarNewsProvider:
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

