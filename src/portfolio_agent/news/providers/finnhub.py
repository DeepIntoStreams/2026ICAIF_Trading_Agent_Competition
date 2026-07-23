"""Finnhub company news provider."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.news.normalizer import normalize_finnhub_company_news


FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


def _get_json(url: str, params: dict[str, Any], timeout_seconds: float) -> Any:
    full_url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        full_url,
        headers={"User-Agent": "portfolio-agent-evaluator/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


class FinnhubCompanyNewsProvider:
    def __init__(self, api_key: str, timeout_seconds: float = 30.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_raw_company_news(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        payload = _get_json(
            FINNHUB_COMPANY_NEWS_URL,
            {
                "symbol": ticker,
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "token": self.api_key,
            },
            self.timeout_seconds,
        )
        return list(payload or [])

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
