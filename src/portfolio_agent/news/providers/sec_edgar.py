"""SEC EDGAR provider - real implementation.

Fetches a company's recent filings from the free, official SEC EDGAR submissions API
and turns each into a NewsRecord. The value over headline feeds is the timestamp: EDGAR
publishes `acceptanceDateTime`, the exact instant a filing became public. We use it as
BOTH `published_at_utc` and `available_at_utc`, so this source has an authoritative,
non-forgeable no-leakage boundary - the single biggest weakness of the Finnhub backfill
(where available == published because there was no true first-seen time).

Endpoints (no API key; SEC requires a descriptive User-Agent with contact email):
- Ticker -> CIK map:  https://www.sec.gov/files/company_tickers.json
- Filings:            https://data.sec.gov/submissions/CIK##########.json

Rate limit: SEC fair-access is ~10 requests/second; this client stays well under that.
"""

from __future__ import annotations

import json
import time as _time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from portfolio_agent.news.models import NewsRecord, stable_content_hash

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"

# Filing forms worth treating as market-relevant events. Extend as needed.
DEFAULT_FORMS = ("8-K", "10-Q", "10-K", "6-K", "SC 13D", "SC 13G", "4")


def _get(url: str, user_agent: str, timeout_seconds: float,
         max_retries: int = 4, sleep=_time.sleep) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == max_retries:
                raise
        except urllib.error.URLError:
            if attempt == max_retries:
                raise
        sleep(1.5 ** attempt)


def _parse_dt_utc(value: str, fallback_date: date) -> datetime:
    """Parse an EDGAR acceptanceDateTime to an aware UTC datetime."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime(fallback_date.year, fallback_date.month, fallback_date.day,
                        tzinfo=timezone.utc)


class SecEdgarNewsProvider:
    """Provider-compatible SEC EDGAR filing source.

    Requires a real contact email in `user_agent` per SEC policy, e.g.
    "portfolio-agent you@example.com".
    """

    def __init__(
        self,
        user_agent: str = "portfolio-agent-evaluator contact@example.com",
        timeout_seconds: float = 30.0,
        forms: tuple[str, ...] = DEFAULT_FORMS,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.forms = set(forms)
        self._cik_map: dict[str, str] | None = None

    # -- ticker -> zero-padded 10-digit CIK ---------------------------------
    def _load_cik_map(self) -> dict[str, str]:
        if self._cik_map is None:
            data = _get(COMPANY_TICKERS_URL, self.user_agent, self.timeout_seconds)
            self._cik_map = {
                str(row["ticker"]).upper(): f"{int(row['cik_str']):010d}"
                for row in data.values()
            }
        return self._cik_map

    def cik_for(self, ticker: str) -> str | None:
        return self._load_cik_map().get(ticker.upper())

    # -- filings -> NewsRecord ----------------------------------------------
    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        cik = self.cik_for(ticker)
        if cik is None:
            return []
        subs = _get(SUBMISSIONS_URL.format(cik10=cik), self.user_agent,
                    self.timeout_seconds)
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        accept_dts = recent.get("acceptanceDateTime", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_desc = recent.get("primaryDocDescription", [])

        records: list[NewsRecord] = []
        for i, form in enumerate(forms):
            if self.forms and form not in self.forms:
                continue
            try:
                fdate = date.fromisoformat(filing_dates[i])
            except (ValueError, IndexError):
                continue
            if not (start_date <= fdate <= end_date):
                continue

            accept_raw = accept_dts[i] if i < len(accept_dts) else filing_dates[i]
            accepted = _parse_dt_utc(accept_raw, fallback_date=fdate)

            acc = accession[i] if i < len(accession) else ""
            doc = primary_docs[i] if i < len(primary_docs) else ""
            desc = primary_desc[i] if i < len(primary_desc) else ""
            acc_nodash = acc.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
                if doc else
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            )
            headline = f"{ticker} files {form}" + (f": {desc}" if desc else "")
            payload = {"provider": "sec_edgar", "accession": acc, "form": form}

            records.append(NewsRecord(
                provider="sec_edgar",
                provider_news_id=acc or f"{cik}-{i}",
                published_at_utc=accepted,        # authoritative public time
                fetched_at_utc=fetched_at_utc,
                first_seen_at_utc=accepted,        # filing is public at acceptance
                available_at_utc=accepted,         # -> non-forgeable no-leakage boundary
                tickers=[ticker.upper()],
                company_names=[company_name],
                headline=headline,
                summary=f"SEC {form} filing accepted {accepted.isoformat()}.",
                source="SEC EDGAR",
                url=url,
                content_hash=stable_content_hash(payload),
                raw_path="",
            ))
        return records
