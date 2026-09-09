"""Point-in-time SEC EDGAR fundamentals for the live daily workflow."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.request import Request, urlopen

from psycopg.types.json import Jsonb

from .store import CompetitionStore, now_utc


SEC_SOURCE = "SEC_EDGAR"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ELIGIBLE_FORMS = frozenset({"10-Q", "10-Q/A", "10-K", "10-K/A"})


@dataclass(frozen=True)
class FundamentalRecord:
    ticker: str
    period_end: date
    available_at: datetime
    filing_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class FundamentalCheckResult:
    checked_tickers: int
    fetched_records: int
    inserted_records: int
    errors: dict[str, str]


class SecEdgarFundamentals:
    """Fetch filing-versioned US-GAAP facts with EDGAR acceptance timestamps."""

    source = SEC_SOURCE

    def __init__(
        self,
        user_agent: str,
        *,
        timeout_seconds: float = 20.0,
        request_interval_seconds: float = 0.11,
        get_json: Callable[[str], dict[str, Any]] | None = None,
    ):
        if not user_agent.strip():
            raise ValueError("SEC_EDGAR_USER_AGENT must identify the organizer and contact")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.request_interval_seconds = request_interval_seconds
        self._custom_get_json = get_json

    def fetch(
        self,
        tickers: list[str],
        checked_at: datetime,
        known_filing_ids: dict[str, set[str]] | None = None,
    ) -> tuple[list[FundamentalRecord], dict[str, str]]:
        checked_at = _aware_utc(checked_at)
        known_filing_ids = known_filing_ids or {}
        mapping = self._ticker_ciks()
        records: list[FundamentalRecord] = []
        errors: dict[str, str] = {}
        for ticker in tickers:
            cik = mapping.get(ticker.upper())
            if cik is None:
                errors[ticker] = "ticker is absent from SEC ticker mapping"
                continue
            try:
                submissions = self._get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
                eligible_ids = _eligible_filing_ids(submissions, checked_at)
                new_ids = eligible_ids - known_filing_ids.get(ticker, set())
                if not new_ids:
                    continue
                company_facts = self._get_json(SEC_COMPANY_FACTS_URL.format(cik=cik))
                records.extend(
                    record for record in _records_from_sec_documents(
                        ticker, cik, submissions, company_facts, checked_at,
                    ) if record.filing_id in new_ids
                )
            except Exception as exc:
                errors[ticker] = f"{type(exc).__name__}: {exc}"
        return records, errors

    def _ticker_ciks(self) -> dict[str, str]:
        document = self._get_json(SEC_TICKERS_URL)
        if "fields" in document and "data" in document:
            fields = [str(value) for value in document["fields"]]
            ticker_index = fields.index("ticker")
            cik_index = fields.index("cik")
            return {
                str(row[ticker_index]).upper(): str(row[cik_index]).zfill(10)
                for row in document["data"]
            }
        # Retain compatibility with SEC's older company_tickers.json shape.
        return {
            str(item["ticker"]).upper(): str(item["cik_str"]).zfill(10)
            for item in document.values()
        }

    def _get_json(self, url: str) -> dict[str, Any]:
        if self._custom_get_json is not None:
            return self._custom_get_json(url)
        request = Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })
        with urlopen(request, timeout=self.timeout_seconds) as response:
            document = json.loads(response.read().decode("utf-8"))
        if self.request_interval_seconds:
            time.sleep(self.request_interval_seconds)
        return document


def check_and_import_fundamentals(
    store: CompetitionStore,
    trading_date: date,
    provider: SecEdgarFundamentals,
    *,
    checked_at: datetime | None = None,
) -> FundamentalCheckResult:
    """Check every active ticker and insert only newly available filings.

    A clean check with no new filing is a successful no-op. Per-ticker provider
    errors are reported and audited but do not block the market close workflow.
    """
    checked_at = _aware_utc(checked_at or now_utc())
    with store._connect() as connection:
        day = connection.execute(
            "SELECT id FROM trading_days WHERE trading_date=%s", (trading_date,),
        ).fetchone()
        if day is None:
            raise KeyError(f"trading day is not configured: {trading_date}")
        instruments = connection.execute(
            "SELECT id, ticker FROM instruments WHERE is_active ORDER BY id"
        ).fetchall()
    ticker_to_id = {str(row["ticker"]): int(row["id"]) for row in instruments}
    with store._connect() as connection:
        existing = connection.execute(
            """SELECT i.ticker, fr.filing_id
                 FROM fundamental_records fr
                 JOIN instruments i ON i.id=fr.instrument_id
                WHERE fr.source=%s AND i.id = ANY(%s)""",
            (provider.source, list(ticker_to_id.values())),
        ).fetchall()
    known_filing_ids: dict[str, set[str]] = {}
    for row in existing:
        known_filing_ids.setdefault(str(row["ticker"]), set()).add(str(row["filing_id"]))
    records, errors = provider.fetch(
        list(ticker_to_id), checked_at, known_filing_ids,
    )
    received_at = now_utc()
    inserted = 0
    with store._connect() as connection:
        for record in records:
            instrument_id = ticker_to_id.get(record.ticker)
            if instrument_id is None:
                errors[record.ticker] = "provider returned an inactive instrument"
                continue
            row = connection.execute(
                """INSERT INTO fundamental_records
                       (instrument_id, period_end, available_at, source, filing_id,
                        payload_json, received_at, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (instrument_id, source, filing_id) DO NOTHING
                   RETURNING id""",
                (instrument_id, record.period_end, record.available_at,
                 provider.source, record.filing_id, Jsonb(record.payload),
                 received_at, received_at),
            ).fetchone()
            inserted += int(row is not None)
        store._audit(
            connection, None, int(day["id"]), "SYSTEM", provider.source,
            "FUNDAMENTALS_CHECKED", "trading_day", int(day["id"]), None,
            {
                "checked_tickers": len(ticker_to_id),
                "fetched_records": len(records),
                "inserted_records": inserted,
                "errors": errors,
                "checked_at": checked_at.isoformat(),
            },
        )
    return FundamentalCheckResult(
        len(ticker_to_id), len(records), inserted, errors,
    )


def _records_from_sec_documents(
    ticker: str,
    cik: str,
    submissions: dict[str, Any],
    company_facts: dict[str, Any],
    checked_at: datetime,
) -> list[FundamentalRecord]:
    recent = submissions.get("filings", {}).get("recent", {})
    keys = (
        "accessionNumber", "form", "reportDate", "filingDate",
        "acceptanceDateTime", "primaryDocument",
    )
    columns = {key: recent.get(key, []) for key in keys}
    row_count = len(columns["accessionNumber"])
    records: list[FundamentalRecord] = []
    for index in range(row_count):
        row = {
            key: values[index] if index < len(values) else None
            for key, values in columns.items()
        }
        if row["form"] not in ELIGIBLE_FORMS or not row["reportDate"]:
            continue
        try:
            accepted_at = _parse_sec_timestamp(row["acceptanceDateTime"])
            period_end = date.fromisoformat(str(row["reportDate"]))
        except (TypeError, ValueError):
            continue
        if accepted_at > checked_at:
            continue
        accession = str(row["accessionNumber"])
        metrics = _extract_metrics(
            company_facts, accession, period_end, str(row["form"]),
        )
        if not metrics:
            continue
        accession_compact = accession.replace("-", "")
        primary_document = str(row["primaryDocument"] or "")
        records.append(FundamentalRecord(
            ticker=ticker,
            period_end=period_end,
            # The EDGAR acceptance timestamp is the information-release
            # boundary; period_end is never used as availability.
            available_at=accepted_at,
            filing_id=accession,
            payload={
                "ticker": ticker,
                "cik": cik,
                "accession_number": accession,
                "form": row["form"],
                "period_end": period_end.isoformat(),
                "filed_date": row["filingDate"],
                "accepted_at_utc": accepted_at.isoformat(),
                "primary_document": primary_document,
                "filing_url": (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{accession_compact}/{primary_document}"
                ),
                "metrics": metrics,
            },
        ))
    return records


def _eligible_filing_ids(
    submissions: dict[str, Any], checked_at: datetime,
) -> set[str]:
    recent = submissions.get("filings", {}).get("recent", {})
    accession_numbers = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    accepted_times = recent.get("acceptanceDateTime", [])
    eligible: set[str] = set()
    for index, accession in enumerate(accession_numbers):
        if index >= len(forms) or forms[index] not in ELIGIBLE_FORMS:
            continue
        try:
            accepted_at = _parse_sec_timestamp(accepted_times[index])
        except (IndexError, TypeError, ValueError):
            continue
        if accepted_at <= checked_at:
            eligible.add(str(accession))
    return eligible


_METRIC_CONCEPTS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet", "Revenues",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_income": ("OperatingIncomeLoss",),
    "total_assets": ("Assets",),
    "total_debt": (
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebt", "LongTermDebtNoncurrent",
    ),
    "stockholders_equity": (
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ),
}


def _extract_metrics(
    company_facts: dict[str, Any], accession: str,
    period_end: date, form: str,
) -> dict[str, float | int]:
    concepts = company_facts.get("facts", {}).get("us-gaap", {})
    metrics: dict[str, float | int] = {}
    for metric, aliases in _METRIC_CONCEPTS.items():
        value = _select_fact(
            concepts, aliases, accession, period_end,
            prefer_longest=form.startswith("10-K"),
        )
        if value is not None:
            metrics[metric] = value
    if "operating_cash_flow" in metrics and "capital_expenditure" in metrics:
        metrics["free_cash_flow"] = (
            metrics["operating_cash_flow"] - metrics["capital_expenditure"]
        )
    return metrics


def _select_fact(
    concepts: dict[str, Any], aliases: tuple[str, ...], accession: str,
    period_end: date, *, prefer_longest: bool,
) -> float | int | None:
    for concept in aliases:
        units = concepts.get(concept, {}).get("units", {})
        candidates = []
        for unit_name in ("USD", "USD/shares", "pure"):
            for fact in units.get(unit_name, []):
                if fact.get("accn") != accession or fact.get("end") != period_end.isoformat():
                    continue
                value = fact.get("val")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    candidates.append(fact)
        if not candidates:
            continue
        duration_rows = [item for item in candidates if item.get("start")]
        if duration_rows:
            def duration(item: dict[str, Any]) -> int:
                return (period_end - date.fromisoformat(str(item["start"]))).days
            candidates = sorted(duration_rows, key=duration, reverse=prefer_longest)
        return candidates[0]["val"]
    return None


def _parse_sec_timestamp(value: object) -> datetime:
    if not value:
        raise ValueError("missing SEC acceptance timestamp")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("SEC acceptance timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)
