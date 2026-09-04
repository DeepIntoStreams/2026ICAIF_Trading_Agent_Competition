"""Yahoo Finance adapter and atomic PostgreSQL import for official daily bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

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


class YahooDailyBars:
    """Small replaceable market-provider boundary based on yfinance."""

    source = "YAHOO_FINANCE"

    def fetch(self, tickers: list[str], trading_date: date) -> list[DailyBar]:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - installation concern
            raise RuntimeError("install yfinance (the project's 'data' extra)") from exc

        end = trading_date + timedelta(days=1)
        bars: list[DailyBar] = []
        errors: list[str] = []
        for ticker in tickers:
            frame = yf.Ticker(ticker).history(
                start=trading_date.isoformat(), end=end.isoformat(),
                interval="1d", auto_adjust=False, actions=False,
            )
            if frame.empty:
                errors.append(f"{ticker}: no row")
                continue
            returned_date = frame.index[-1].date()
            if returned_date != trading_date:
                errors.append(f"{ticker}: returned {returned_date}, expected {trading_date}")
                continue
            row = frame.iloc[-1]
            values = {name: Decimal(str(row[name])) for name in
                      ("Open", "High", "Low", "Close", "Volume")}
            adjusted_close = Decimal(str(row.get("Adj Close", row["Close"])))
            factor = adjusted_close / values["Close"]
            bar = DailyBar(
                ticker=ticker, open=values["Open"], high=values["High"],
                low=values["Low"], close=values["Close"],
                adjusted_open=values["Open"] * factor,
                adjusted_close=adjusted_close, volume=values["Volume"],
            )
            if min(bar.open, bar.high, bar.low, bar.close,
                   bar.adjusted_open, bar.adjusted_close) <= 0 or bar.volume < 0:
                errors.append(f"{ticker}: invalid OHLCV")
                continue
            bars.append(bar)
        if errors:
            raise RuntimeError("incomplete Yahoo data: " + "; ".join(errors))
        return bars


def import_market_day(
    store: CompetitionStore,
    trading_date: date,
    provider: YahooDailyBars | None = None,
) -> int:
    """Fetch every active instrument, then commit the complete day atomically."""
    provider = provider or YahooDailyBars()
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
    if not instruments:
        raise RuntimeError("no active instruments are configured")

    try:
        bars = provider.fetch([str(row["ticker"]) for row in instruments], trading_date)
    except Exception:
        with store._connect() as connection:
            connection.execute(
                "UPDATE trading_days SET market_status='FAILED', updated_at=%s WHERE id=%s",
                (now_utc(), day["id"]),
            )
        raise
    by_ticker = {bar.ticker: bar for bar in bars}
    missing = [str(row["ticker"]) for row in instruments if row["ticker"] not in by_ticker]
    if missing:
        raise RuntimeError("provider omitted active instruments: " + ", ".join(missing))

    received_at = now_utc()
    try:
        with store._connect() as connection:
            locked_day = connection.execute(
                "SELECT id FROM trading_days WHERE id=%s FOR UPDATE", (day["id"],)
            ).fetchone()
            for instrument in instruments:
                bar = by_ticker[str(instrument["ticker"])]
                connection.execute(
                    """INSERT INTO market_bars
                           (trading_day_id, instrument_id, open, high, low, close,
                            adjusted_open, adjusted_close, volume, source,
                            received_at, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (trading_day_id, instrument_id) DO NOTHING""",
                    (locked_day["id"], instrument["id"], bar.open, bar.high, bar.low,
                     bar.close, bar.adjusted_open, bar.adjusted_close, bar.volume,
                     provider.source, received_at, received_at),
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
            store._audit(connection, None, locked_day["id"], "SYSTEM", provider.source,
                         "MARKET_DATA_IMPORTED", "trading_day", locked_day["id"], None,
                         {"bar_count": count, "source": provider.source})
            return int(count)
    except Exception:
        # The data transaction rolls back. Record the workflow failure separately.
        with store._connect() as connection:
            connection.execute(
                "UPDATE trading_days SET market_status='FAILED', updated_at=%s WHERE id=%s",
                (datetime.now(timezone.utc), day["id"]),
            )
        raise
