"""Provider protocol for company news ingestion."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from portfolio_agent.news.models import NewsRecord


class NewsProvider(Protocol):
    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        ...

