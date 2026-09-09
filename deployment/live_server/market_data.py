"""Multi-source verification and atomic PostgreSQL import for daily bars."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .store import CompetitionStore, now_utc


@dataclass(frozen=True)
class DailyBar:
    ticker: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_open: Decimal
    adjusted_close: Decimal
    volume: Decimal


class DailyBarsProvider(Protocol):
    source: str

    def fetch(self, tickers: list[str], trading_date: date) -> list[DailyBar]: ...


class MarketDataVerificationError(RuntimeError):
    """Raised before any official bar is written when sources do not corroborate."""


@dataclass(frozen=True)
class VerifiedMarketDay:
    bars: tuple[DailyBar, ...]
    selected_sources: dict[str, tuple[str, ...]]
    candidates: dict[str, dict[str, DailyBar]]
    provider_errors: dict[str, str]
    verification_status: dict[str, str]
    is_tradable: dict[str, bool]
    market_event_ids: dict[str, int | None]


@dataclass(frozen=True)
class ConfirmedNoTradingEvent:
    """Organizer-confirmed full-session event; never inferred from missing bars."""

    id: int
    ticker: str
    event_type: str
    confirmation_source: str
    confirmation_reference: str


class YahooDailyBars:
    """Replaceable market provider based on yfinance."""

    source = "YAHOO_FINANCE"

    def fetch(self, tickers: list[str], trading_date: date) -> list[DailyBar]:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - installation concern
            raise RuntimeError("install yfinance (the project's 'data' extra)") from exc

        end = trading_date + timedelta(days=1)
        bars: list[DailyBar] = []
        for ticker in tickers:
            try:
                frame = yf.Ticker(ticker).history(
                    start=trading_date.isoformat(), end=end.isoformat(),
                    interval="1d", auto_adjust=False, actions=False,
                )
                if frame.empty or frame.index[-1].date() != trading_date:
                    continue
                row = frame.iloc[-1]
                values = {
                    name: _decimal(row[name])
                    for name in ("Open", "High", "Low", "Close", "Volume")
                }
                adjusted_close = _decimal(row.get("Adj Close", row["Close"]))
                factor = adjusted_close / values["Close"]
                bars.append(DailyBar(
                    ticker=ticker,
                    open=values["Open"], high=values["High"], low=values["Low"],
                    close=values["Close"], adjusted_open=values["Open"] * factor,
                    adjusted_close=adjusted_close, volume=values["Volume"],
                ))
            except Exception:
                # Verification reports the symbol as missing from this source.
                # One bad symbol must not discard usable candidates for all others.
                continue
        return bars


class MassiveDailyBars:
    """Independent grouped daily-bar adapter (formerly Polygon.io)."""

    source = "MASSIVE"
    base_url = "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks"

    def __init__(self, api_key: str, *, timeout_seconds: float = 20.0):
        if not api_key.strip():
            raise ValueError("MASSIVE_API_KEY is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch(self, tickers: list[str], trading_date: date) -> list[DailyBar]:
        query = urlencode({"adjusted": "false", "apiKey": self.api_key})
        request = Request(
            f"{self.base_url}/{trading_date.isoformat()}?{query}",
            headers={"User-Agent": "ICAIF2026-market-verifier/1.0",
                     "Accept": "application/json"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            document = json.loads(response.read().decode("utf-8"))
        if document.get("status") not in {"OK", "DELAYED"}:
            raise RuntimeError(
                f"Massive returned status={document.get('status')!r}: "
                f"{document.get('error') or document.get('message') or 'unknown error'}"
            )
        wanted = set(tickers)
        bars: list[DailyBar] = []
        for row in document.get("results") or []:
            ticker = str(row.get("T") or "")
            if ticker not in wanted:
                continue
            close = _decimal(row["c"])
            open_price = _decimal(row["o"])
            bars.append(DailyBar(
                ticker=ticker,
                open=open_price, high=_decimal(row["h"]), low=_decimal(row["l"]),
                close=close,
                # The request explicitly asks for as-traded (unadjusted) bars.
                adjusted_open=open_price, adjusted_close=close,
                volume=_decimal(row["v"]),
            ))
        return bars


class TwelveDataDailyBars:
    """Batch daily-bar adapter for Twelve Data's time-series endpoint."""

    source = "TWELVE_DATA"
    base_url = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key: str, *, timeout_seconds: float = 30.0):
        if not api_key.strip():
            raise ValueError("TWELVE_DATA_API_KEY is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch(self, tickers: list[str], trading_date: date) -> list[DailyBar]:
        query = urlencode({
            "symbol": ",".join(tickers),
            "interval": "1day",
            "start_date": trading_date.isoformat(),
            "end_date": (trading_date + timedelta(days=1)).isoformat(),
            "adjust": "none",
            "order": "desc",
            "apikey": self.api_key,
        })
        request = Request(
            f"{self.base_url}?{query}",
            headers={"User-Agent": "ICAIF2026-market-verifier/1.0",
                     "Accept": "application/json"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            document = json.loads(response.read().decode("utf-8"))
        if document.get("status") == "error":
            raise RuntimeError(
                "Twelve Data error: "
                + str(document.get("message") or document.get("code") or "unknown")
            )

        # Batch responses are keyed by symbol. Keep single-symbol response
        # support so the adapter is independently testable and reusable.
        if "values" in document and len(tickers) == 1:
            payload_by_ticker = {tickers[0]: document}
        else:
            payload_by_ticker = {
                str(key).upper(): value for key, value in document.items()
                if isinstance(value, dict)
            }

        bars: list[DailyBar] = []
        for ticker in tickers:
            payload = payload_by_ticker.get(ticker.upper())
            if payload is None:
                continue
            if payload.get("status") == "error":
                continue
            exact_rows = [
                row for row in payload.get("values") or []
                if str(row.get("datetime", ""))[:10] == trading_date.isoformat()
            ]
            if len(exact_rows) != 1:
                continue
            row = exact_rows[0]
            try:
                open_price = _decimal(row["open"])
                close = _decimal(row["close"])
                bars.append(DailyBar(
                    ticker=ticker,
                    open=open_price, high=_decimal(row["high"]),
                    low=_decimal(row["low"]), close=close,
                    # adjust=none makes this an as-traded corroboration source.
                    adjusted_open=open_price, adjusted_close=close,
                    volume=_decimal(row["volume"]),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                continue
        return bars


class VerifiedDailyBars:
    """Require a per-ticker quorum of independent daily-bar providers."""

    source = "MULTI_SOURCE_CONSENSUS"

    def __init__(
        self,
        providers: list[DailyBarsProvider],
        *,
        minimum_sources: int = 2,
        price_relative_tolerance: Decimal = Decimal("0.005"),
        volume_relative_tolerance: Decimal = Decimal("0.15"),
    ):
        if not 2 <= minimum_sources <= 3:
            raise ValueError("minimum_sources must be 2 or 3")
        if len(providers) < minimum_sources or len(providers) > 3:
            raise ValueError("configure between minimum_sources and 3 providers")
        names = [provider.source for provider in providers]
        if len(names) != len(set(names)):
            raise ValueError("market provider source names must be unique")
        self.providers = tuple(providers)
        self.minimum_sources = minimum_sources
        self.price_relative_tolerance = price_relative_tolerance
        self.volume_relative_tolerance = volume_relative_tolerance

    def fetch(
        self,
        tickers: list[str],
        trading_date: date,
        *,
        confirmed_no_trading: dict[str, ConfirmedNoTradingEvent] | None = None,
        reference_bars: dict[str, DailyBar] | None = None,
    ) -> VerifiedMarketDay:
        expected = set(tickers)
        confirmed_no_trading = confirmed_no_trading or {}
        reference_bars = reference_bars or {}
        candidates: dict[str, dict[str, DailyBar]] = {}
        provider_errors: dict[str, str] = {}
        for provider in self.providers:
            try:
                fetched = provider.fetch(tickers, trading_date)
                by_ticker = _validated_provider_batch(provider.source, fetched, expected)
            except Exception as exc:
                provider_errors[provider.source] = f"{type(exc).__name__}: {exc}"
                continue
            candidates[provider.source] = by_ticker
            missing = sorted(expected - set(by_ticker))
            if missing:
                provider_errors[provider.source] = f"missing tickers: {missing}"

        source_order = [
            provider.source for provider in self.providers
            if provider.source in candidates
        ]
        selected_bars: list[DailyBar] = []
        selected_sources: dict[str, tuple[str, ...]] = {}
        verification_status: dict[str, str] = {}
        is_tradable: dict[str, bool] = {}
        market_event_ids: dict[str, int | None] = {}
        disagreements: list[str] = []
        primary_source = self.providers[0].source
        for ticker in tickers:
            available = [
                (source, candidates[source][ticker])
                for source in source_order if ticker in candidates[source]
            ]
            event = confirmed_no_trading.get(ticker)
            if event is not None:
                reference = reference_bars.get(ticker)
                if reference is None:
                    disagreements.append(
                        f"{ticker}: confirmed {event.event_type} has no prior trusted bar"
                    )
                    continue
                conflicting = [
                    source for source, candidate in available
                    if not self._represents_no_trading(candidate, reference)
                ]
                if conflicting:
                    disagreements.append(
                        f"{ticker}: confirmed {event.event_type} conflicts with "
                        f"traded data from {conflicting}"
                    )
                    continue
                selected_bars.append(_no_trading_bar(ticker, reference))
                selected_sources[ticker] = ()
                verification_status[ticker] = "CONFIRMED_NO_TRADING"
                is_tradable[ticker] = False
                market_event_ids[ticker] = event.id
                continue

            agreeing = self._largest_agreeing_group(
                available
            )
            if len(agreeing) < self.minimum_sources:
                disagreements.append(
                    f"{ticker}: no {self.minimum_sources}-source quorum; "
                    + ", ".join(
                        f"{source}={_compact_bar(candidate)}"
                        for source, candidate in available
                    )
                )
                continue
            chosen_source = next(source for source in source_order if source in agreeing)
            selected_bars.append(candidates[chosen_source][ticker])
            selected_sources[ticker] = tuple(agreeing)
            if primary_source in agreeing:
                verification_status[ticker] = f"VERIFIED_{len(agreeing)}"
            elif ticker in candidates.get(primary_source, {}):
                verification_status[ticker] = "PRIMARY_DISAGREEMENT"
            else:
                verification_status[ticker] = "PRIMARY_MISSING"
            is_tradable[ticker] = True
            market_event_ids[ticker] = None

        if disagreements:
            raise MarketDataVerificationError(
                "daily-bar disagreement: " + "; ".join(disagreements)
            )
        return VerifiedMarketDay(
            tuple(selected_bars), selected_sources, candidates, provider_errors,
            verification_status, is_tradable, market_event_ids,
        )

    def _largest_agreeing_group(
        self, values: list[tuple[str, DailyBar]],
    ) -> tuple[str, ...]:
        # At most three providers are allowed, so exhaustive subsets keep the
        # quorum rule explicit. Every pair in a selected quorum must agree.
        best: tuple[str, ...] = ()
        count = len(values)
        for mask in range(1, 1 << count):
            group = tuple(values[index] for index in range(count) if mask & (1 << index))
            if len(group) < len(best):
                continue
            if all(
                self._bars_agree(left[1], right[1])
                for position, left in enumerate(group)
                for right in group[position + 1:]
            ):
                names = tuple(item[0] for item in group)
                if len(names) > len(best):
                    best = names
        return best

    def _bars_agree(self, left: DailyBar, right: DailyBar) -> bool:
        price_fields = ("open", "high", "low", "close")
        return all(
            _relative_difference(getattr(left, field), getattr(right, field))
            <= self.price_relative_tolerance
            for field in price_fields
        ) and (
            _relative_difference(left.volume, right.volume)
            <= self.volume_relative_tolerance
        )

    def _represents_no_trading(self, candidate: DailyBar, reference: DailyBar) -> bool:
        return (
            candidate.volume == 0
            and candidate.open == candidate.high == candidate.low == candidate.close
            and _relative_difference(candidate.close, reference.close)
            <= self.price_relative_tolerance
        )


def configured_market_verifier() -> VerifiedDailyBars:
    """Build the production 2-of-3 verifier."""
    minimum = int(os.environ.get("MARKET_DATA_MIN_SOURCES", "2"))
    massive_api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not massive_api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for three-source market verification")
    twelve_data_api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not twelve_data_api_key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is required for three-source market verification"
        )
    return VerifiedDailyBars(
        [
            YahooDailyBars(),
            MassiveDailyBars(massive_api_key),
            TwelveDataDailyBars(twelve_data_api_key),
        ],
        minimum_sources=minimum,
    )


def import_market_day(
    store: CompetitionStore,
    trading_date: date,
    verifier: VerifiedDailyBars | None = None,
) -> int:
    """Verify every active instrument, then commit the complete day atomically."""
    verifier = verifier or configured_market_verifier()
    with store._connect() as connection:
        day = connection.execute(
            "SELECT id, market_status FROM trading_days WHERE trading_date=%s",
            (trading_date,),
        ).fetchone()
        if not day:
            raise KeyError(f"trading day is not configured: {trading_date}")
        instruments = connection.execute(
            "SELECT id, ticker FROM instruments WHERE is_active ORDER BY id"
        ).fetchall()
        existing_count = connection.execute(
            "SELECT count(*) AS n FROM market_bars WHERE trading_day_id=%s",
            (day["id"],),
        ).fetchone()["n"]
        event_rows = connection.execute(
            """SELECT event.id, instrument.ticker, event.event_type,
                      event.confirmation_source, event.confirmation_reference
                 FROM instrument_market_events event
                 JOIN instruments instrument ON instrument.id=event.instrument_id
                WHERE event.trading_day_id=%s
                  AND event.trading_status='NON_TRADABLE'
                  AND event.confirmed_at IS NOT NULL""",
            (day["id"],),
        ).fetchall()
    if not instruments:
        raise RuntimeError("no active instruments are configured")
    if day["market_status"] == "DATA_IMPORTED" and existing_count == len(instruments):
        return int(existing_count)
    if existing_count:
        exc = MarketDataVerificationError(
            f"refusing to merge a partial market day: {existing_count}/"
            f"{len(instruments)} bars already exist; operator action required"
        )
        _record_market_failure(store, int(day["id"]), exc)
        raise exc

    tickers = [str(row["ticker"]) for row in instruments]
    confirmed_no_trading = {
        str(row["ticker"]): ConfirmedNoTradingEvent(
            id=int(row["id"]),
            ticker=str(row["ticker"]),
            event_type=str(row["event_type"]),
            confirmation_source=str(row["confirmation_source"]),
            confirmation_reference=str(row["confirmation_reference"]),
        )
        for row in event_rows
    }
    reference_bars: dict[str, DailyBar] = {}
    if confirmed_no_trading:
        with store._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT ON (bar.instrument_id)
                          instrument.ticker, bar.open, bar.high, bar.low, bar.close,
                          bar.adjusted_open, bar.adjusted_close, bar.volume
                     FROM market_bars bar
                     JOIN trading_days prior_day ON prior_day.id=bar.trading_day_id
                     JOIN instruments instrument ON instrument.id=bar.instrument_id
                    WHERE instrument.ticker=ANY(%s)
                      AND prior_day.trading_date<%s
                      AND bar.is_tradable
                    ORDER BY bar.instrument_id, prior_day.trading_date DESC""",
                (list(confirmed_no_trading), trading_date),
            ).fetchall()
        reference_bars = {
            str(row["ticker"]): DailyBar(
                ticker=str(row["ticker"]),
                open=_decimal(row["open"]), high=_decimal(row["high"]),
                low=_decimal(row["low"]), close=_decimal(row["close"]),
                adjusted_open=_decimal(row["adjusted_open"]),
                adjusted_close=_decimal(row["adjusted_close"]),
                volume=_decimal(row["volume"]),
            )
            for row in rows
        }
    try:
        verified = verifier.fetch(
            tickers,
            trading_date,
            confirmed_no_trading=confirmed_no_trading,
            reference_bars=reference_bars,
        )
    except Exception as exc:
        _record_market_failure(store, int(day["id"]), exc)
        raise

    by_ticker = {bar.ticker: bar for bar in verified.bars}
    received_at = now_utc()
    try:
        with store._connect() as connection:
            locked_day = connection.execute(
                "SELECT id FROM trading_days WHERE id=%s FOR UPDATE", (day["id"],)
            ).fetchone()
            instrument_by_ticker = {
                str(row["ticker"]): int(row["id"]) for row in instruments
            }
            selected_by_ticker = {
                ticker: set(sources)
                for ticker, sources in verified.selected_sources.items()
            }
            for source, source_bars in verified.candidates.items():
                for ticker, candidate in source_bars.items():
                    connection.execute(
                        """INSERT INTO market_bar_candidates
                               (trading_day_id, instrument_id, source, open, high, low,
                                close, adjusted_open, adjusted_close, volume, selected,
                                received_at, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (trading_day_id, instrument_id, source) DO NOTHING""",
                        (locked_day["id"], instrument_by_ticker[ticker], source,
                         candidate.open, candidate.high, candidate.low, candidate.close,
                         candidate.adjusted_open, candidate.adjusted_close, candidate.volume,
                         source in selected_by_ticker[ticker], received_at, received_at),
                    )
            for instrument in instruments:
                ticker = str(instrument["ticker"])
                bar = by_ticker[ticker]
                selected = verified.selected_sources[ticker]
                sources = "|".join(selected) if selected else "CONFIRMED_NO_TRADING"
                connection.execute(
                    """INSERT INTO market_bars
                           (trading_day_id, instrument_id, open, high, low, close,
                            adjusted_open, adjusted_close, volume, source, is_tradable,
                            verification_status, market_event_id, received_at, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s)
                       ON CONFLICT (trading_day_id, instrument_id) DO NOTHING""",
                    (locked_day["id"], instrument["id"], bar.open, bar.high, bar.low,
                     bar.close, bar.adjusted_open, bar.adjusted_close, bar.volume,
                     f"CONSENSUS:{sources}", verified.is_tradable[ticker],
                     verified.verification_status[ticker],
                     verified.market_event_ids[ticker], received_at, received_at),
                )
            count = connection.execute(
                "SELECT count(*) AS n FROM market_bars WHERE trading_day_id=%s",
                (locked_day["id"],),
            ).fetchone()["n"]
            if count != len(instruments):
                raise RuntimeError(f"market day incomplete: {count}/{len(instruments)} bars")
            connection.execute(
                "UPDATE trading_days SET market_status='DATA_IMPORTED', updated_at=%s WHERE id=%s",
                (received_at, locked_day["id"]),
            )
            store._audit(
                connection, None, locked_day["id"], "SYSTEM", verifier.source,
                "MARKET_DATA_IMPORTED", "trading_day", locked_day["id"], None,
                {
                    "bar_count": count,
                    "required_sources": verifier.minimum_sources,
                    "providers": list(verified.candidates),
                    "provider_errors": verified.provider_errors,
                    "selected_sources": {
                        ticker: list(sources)
                        for ticker, sources in verified.selected_sources.items()
                    },
                    "verification_status": verified.verification_status,
                    "non_tradable_tickers": sorted(
                        ticker for ticker, tradable in verified.is_tradable.items()
                        if not tradable
                    ),
                },
            )
            return int(count)
    except Exception as exc:
        _record_market_failure(store, int(day["id"]), exc)
        raise


def _validated_provider_batch(
    source: str, bars: list[DailyBar], expected: set[str],
) -> dict[str, DailyBar]:
    by_ticker: dict[str, DailyBar] = {}
    for bar in bars:
        if bar.ticker in by_ticker:
            raise ValueError(f"{source} duplicated {bar.ticker}")
        _validate_bar(bar)
        by_ticker[bar.ticker] = bar
    actual = set(by_ticker)
    if actual - expected:
        raise ValueError(
            f"{source} universe mismatch; unexpected={sorted(actual - expected)}"
        )
    return by_ticker


def _validate_bar(bar: DailyBar) -> None:
    prices = (
        bar.open, bar.high, bar.low, bar.close,
        bar.adjusted_open, bar.adjusted_close,
    )
    if any(not value.is_finite() or value <= 0 for value in prices):
        raise ValueError(f"{bar.ticker}: non-positive or non-finite price")
    if not bar.volume.is_finite() or bar.volume < 0:
        raise ValueError(f"{bar.ticker}: invalid volume")
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        raise ValueError(f"{bar.ticker}: inconsistent OHLC")


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite numeric value: {value!r}")
    return result


def _relative_difference(left: Decimal, right: Decimal) -> Decimal:
    scale = max(abs(left), abs(right), Decimal("1"))
    return abs(left - right) / scale


def _compact_bar(bar: DailyBar) -> str:
    return f"O={bar.open},H={bar.high},L={bar.low},C={bar.close},V={bar.volume}"


def _no_trading_bar(ticker: str, reference: DailyBar) -> DailyBar:
    """Carry the last trusted close for valuation, never as evidence of a trade."""
    return DailyBar(
        ticker=ticker,
        open=reference.close,
        high=reference.close,
        low=reference.close,
        close=reference.close,
        adjusted_open=reference.adjusted_close,
        adjusted_close=reference.adjusted_close,
        volume=Decimal("0"),
    )


def _record_market_failure(store: CompetitionStore, day_id: int, exc: Exception) -> None:
    timestamp = now_utc()
    with store._connect() as connection:
        connection.execute(
            "UPDATE trading_days SET market_status='FAILED', updated_at=%s WHERE id=%s",
            (timestamp, day_id),
        )
        store._audit(
            connection, None, day_id, "SYSTEM", "market_verifier",
            "MARKET_DATA_REJECTED", "trading_day", day_id, None,
            {"error_type": type(exc).__name__, "error": str(exc)[:8000]},
        )
