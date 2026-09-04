from __future__ import annotations

import hashlib
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from deployment.live_server.store import CompetitionStore


DATABASE_URL = os.environ.get("TEST_COMPETITION_DATABASE_URL")
SCHEMA = Path(__file__).parents[2] / "data" / "database" / "schema.sql"


@unittest.skipUnless(DATABASE_URL, "set TEST_COMPETITION_DATABASE_URL for PostgreSQL tests")
class PostgreSQLTestCase(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(SCHEMA.read_text(encoding="utf-8"))
            connection.execute(
                """TRUNCATE audit_logs, daily_performance, cash_ledger, transactions,
                   executions, submission_weights, decision_submissions, submission_attempts,
                   observations, position_snapshots, portfolio_snapshots, fundamental_records,
                   market_bars, trading_days, instruments, teams
                   RESTART IDENTITY CASCADE"""
            )
        self.store = CompetitionStore(DATABASE_URL)
        self.token = self.store.register_team("team_001", "Team 001", "test-api-key")

    def seed_observation(self, signal=date(2026, 9, 1),
                         submission_open_at: datetime | None = None,
                         deadline: datetime | None = None) -> dict:
        close_at = datetime.combine(signal, time(20), timezone.utc)
        next_date = signal + timedelta(days=1)
        submission_open_at = submission_open_at or close_at
        deadline = deadline or datetime.combine(next_date, time(13, 29, 59), timezone.utc)
        self.store.create_trading_day(
            signal,
            datetime.combine(signal, time(13, 30), timezone.utc),
            close_at, submission_open_at, deadline,
        )
        self.store.create_trading_day(
            next_date,
            datetime.combine(next_date, time(13, 30), timezone.utc),
            datetime.combine(next_date, time(20), timezone.utc),
            datetime.combine(next_date, time(20), timezone.utc),
            datetime.combine(next_date + timedelta(days=1), time(13, 29, 59), timezone.utc),
        )
        payload = {
            "session_date": signal.isoformat(),
            "event_time_utc": deadline.isoformat(),
            "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "market_features": {},
            "fundamental_features": {},
            "portfolio": {"weights": {}, "cash_ratio": 1.0, "nav": 1_000_000},
            "constraints": {"long_only": True, "max_asset_weight": 0.1,
                            "max_gross_exposure": 1.0},
            "news": [],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with psycopg.connect(DATABASE_URL) as connection:
            team_id = connection.execute(
                "SELECT id FROM teams WHERE team_code='team_001'").fetchone()[0]
            day_id = connection.execute(
                "SELECT id FROM trading_days WHERE trading_date=%s", (signal,)).fetchone()[0]
            snapshot_id = connection.execute(
                """INSERT INTO portfolio_snapshots
                       (team_id, trading_day_id, snapshot_type, cash, positions_value,
                        nav, gross_exposure, drawdown, effective_at, created_at)
                   VALUES (%s, %s, 'CLOSE', 1000000, 0, 1000000, 0, 0, %s, %s)
                   RETURNING id""",
                (team_id, day_id, close_at, close_at),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO observations
                       (team_id, trading_day_id, close_portfolio_snapshot_id, payload_json,
                        payload_hash, generated_at, published_at, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (team_id, day_id, snapshot_id, Jsonb(payload),
                 hashlib.sha256(canonical.encode()).hexdigest(),
                 submission_open_at, submission_open_at, submission_open_at),
            )
        return payload

    @staticmethod
    def decision(weights=None, team_id=None):
        value = {
            "type": "decision_response", "protocol_version": "0.1",
            "run_id": "official_2026", "session_date": "2026-09-01",
            "target_weights": weights or {"AAPL": 0.05},
        }
        if team_id is not None:
            value["team_id"] = team_id
        return value

    def accepted_submit(self, key="request-1", weights=None):
        return self.store.submit(
            "team_001", self.decision(weights), key,
            datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
        )


class CompetitionStoreTest(PostgreSQLTestCase):
    def test_raw_submission_is_handoff_without_cleaning_or_execution(self):
        self.seed_observation()
        raw = {"AAPL": -0.5, "UNKNOWN": 4.0}
        receipt = self.accepted_submit(weights=raw)
        self.assertTrue(receipt["accepted"])
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute(
                """SELECT raw_payload_json, status, validator_version,
                          stored_weight_count, expected_weight_count
                     FROM decision_submissions"""
            ).fetchone()
            self.assertEqual(row[0]["target_weights"], raw)
            self.assertEqual(row[1], "RECEIVED")
            self.assertIsNone(row[2])
            self.assertEqual(row[3:], (0, 2))
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM submission_weights").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM executions").fetchone()[0], 0)

    def test_idempotency_conflict_and_daily_limit_are_traced(self):
        self.seed_observation()
        first = self.accepted_submit()
        replay = self.accepted_submit()
        conflict = self.accepted_submit(weights={"AAPL": 0.08})
        second = self.accepted_submit("request-2", {"MSFT": 0.05})
        self.assertEqual(replay["id"], first["id"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(conflict["reason"], "idempotency_key_reused")
        self.assertEqual(second["reason"], "decision_already_accepted")
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM decision_submissions").fetchone()[0], 1)
            outcomes = [row[0] for row in connection.execute(
                "SELECT outcome FROM submission_attempts ORDER BY id")]
            self.assertEqual(outcomes, [
                "ACCEPTED", "IDEMPOTENT_REPLAY",
                "IDEMPOTENCY_CONFLICT", "ALREADY_SUBMITTED"])

    def test_invalid_or_late_attempt_does_not_consume_daily_slot(self):
        self.seed_observation()
        self.store.record_invalid_attempt(
            "team_001", "invalid-1", None,
            datetime(2026, 9, 2, 11, tzinfo=timezone.utc),
            {}, hashlib.sha256(b"{}").hexdigest(), "invalid_decision_envelope",
        )
        late = self.store.submit(
            "team_001", self.decision(), "late",
            datetime(2026, 9, 2, 14, tzinfo=timezone.utc),
        )
        accepted = self.accepted_submit("valid")
        self.assertEqual(late["reason"], "outside_submission_window")
        self.assertTrue(accepted["accepted"])

    def test_concurrent_submissions_create_one_canonical_row(self):
        self.seed_observation()
        received = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda key: self.store.submit("team_001", self.decision(), key, received),
                ("parallel-1", "parallel-2"),
            ))
        self.assertEqual(sum(bool(result["accepted"]) for result in results), 1)
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM decision_submissions").fetchone()[0], 1)

    def test_observation_first_serve_and_status(self):
        payload = self.seed_observation()
        status_value = self.store.team_status(
            "team_001", datetime(2026, 9, 2, 12, tzinfo=timezone.utc))
        self.assertTrue(status_value["session"]["observation_available"])
        self.assertEqual(self.store.team_observation("team_001"), payload)
        self.store.team_observation("team_001")
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT first_served_at FROM observations").fetchone()[0])
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM audit_logs "
                "WHERE event_type='OBSERVATION_FIRST_SERVED'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
