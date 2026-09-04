"""Build and publish team observations after Competition closes the day."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from .store import canonical_json


class ObservationService:
    @staticmethod
    def shared_panel(instruments: list[tuple[Any, ...]], trading_date: date,
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
        return {
            "session_date": trading_date.isoformat(),
            "event_time_utc": deadline.isoformat(),
            "assets": assets,
            "market_features": market_features,
            # Keep the point-in-time fundamental hook in the protocol. Data
            # acquisition and feature selection will be added separately.
            "fundamental_features": {},
            "constraints": constraints,
            "news": [],
        }

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
