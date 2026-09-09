"""Build and publish team observations after Competition closes the day."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from .store import canonical_json


class ObservationService:
    @staticmethod
    def shared_panel(connection: Any, instruments: list[tuple[Any, ...]], trading_date: date,
                     deadline: datetime, constraints: dict[str, Any]) -> dict[str, Any]:
        assets = []
        market_features: dict[str, dict[str, float]] = {}
        for row in instruments:
            _, ticker, company_name, sector = row[:4]
            assets.append({
                "ticker": ticker,
                "company_name": company_name,
                "sector": sector or "",
                "open_price": float(row[8]),
            })
            market_features[str(ticker)] = {
                "open": float(row[4]),
                "high": float(row[5]),
                "low": float(row[6]),
                "close": float(row[7]),
                "adjusted_open": float(row[8]),
                "adjusted_close": float(row[9]),
                "volume": float(row[10]),
            }
        # A normal live run occurs before tomorrow's decision deadline. Do not
        # expose a preloaded filing until it was actually public at generation
        # time; on recovery, the frozen deadline remains the upper boundary.
        generated_at = datetime.now(timezone.utc)
        availability_cutoff = min(deadline, generated_at)
        fundamentals = ObservationService._fundamental_features(
            connection,
            [int(row[0]) for row in instruments],
            {int(row[0]): str(row[1]) for row in instruments},
            availability_cutoff,
        )
        return {
            "session_date": trading_date.isoformat(),
            "event_time_utc": deadline.isoformat(),
            "assets": assets,
            "market_features": market_features,
            "fundamental_features": fundamentals,
            "constraints": constraints,
            "news": [],
        }

    @staticmethod
    def _fundamental_features(
        connection: Any,
        instrument_ids: list[int],
        ticker_by_id: dict[int, str],
        cutoff: datetime,
    ) -> dict[str, dict[str, float | bool | None]]:
        if not instrument_ids:
            return {}
        rows = connection.execute(
            """SELECT DISTINCT ON (instrument_id)
                      instrument_id, period_end, available_at, payload_json
                 FROM fundamental_records
                WHERE instrument_id = ANY(%s) AND available_at <= %s
                ORDER BY instrument_id, period_end DESC, available_at DESC, id DESC""",
            (instrument_ids, cutoff),
        ).fetchall()
        result: dict[str, dict[str, float | bool | None]] = {}
        for instrument_id, period_end, available_at, payload in rows:
            metrics = dict((payload or {}).get("metrics") or {})
            if not metrics:
                continue
            form = str((payload or {}).get("form") or "")
            features: dict[str, float | bool | None] = {
                key: _safe_float(metrics.get(key))
                for key in (
                    "revenue", "net_income", "operating_income", "total_assets",
                    "total_debt", "stockholders_equity", "operating_cash_flow",
                    "capital_expenditure", "free_cash_flow",
                )
            }
            features.update({
                "net_margin": _safe_div(metrics.get("net_income"), metrics.get("revenue")),
                "operating_margin": _safe_div(
                    metrics.get("operating_income"), metrics.get("revenue")
                ),
                "roe": _safe_div(
                    metrics.get("net_income"), metrics.get("stockholders_equity")
                ),
                "fcf_margin": _safe_div(
                    metrics.get("free_cash_flow"), metrics.get("revenue")
                ),
                "debt_to_assets": _safe_div(
                    metrics.get("total_debt"), metrics.get("total_assets")
                ),
                "cash_conversion": _safe_div(
                    metrics.get("operating_cash_flow"), metrics.get("net_income")
                ),
                "report_age_days": float((cutoff.date() - period_end).days),
                "stale_fundamental": (cutoff.date() - period_end).days > 180,
                "is_annual": form.startswith("10-K"),
                "is_amendment": form.endswith("/A"),
            })
            result[ticker_by_id[int(instrument_id)]] = features
        return result

    @staticmethod
    def publish(connection: Any, *, team_id: int, trading_day_id: int,
                close_snapshot_id: int, payload: dict[str, Any]) -> int:
        timestamp = datetime.now(timezone.utc)
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return int(connection.execute(
            """INSERT INTO observations
                   (team_id, trading_day_id, close_portfolio_snapshot_id, payload_json,
                    payload_hash, generated_at, published_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (team_id, trading_day_id, close_snapshot_id, Jsonb(payload), digest,
             timestamp, timestamp, timestamp),
        ).fetchone()[0])


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _safe_div(numerator: Any, denominator: Any) -> float | None:
    left, right = _safe_float(numerator), _safe_float(denominator)
    if left is None or right in (None, 0.0):
        return None
    return _safe_float(left / right)
