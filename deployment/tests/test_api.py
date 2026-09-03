from __future__ import annotations

import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from deployment.live_server.app import create_app
from deployment.live_server.store import CompetitionStore
from deployment.tests.test_live_store import DATABASE_URL, PostgreSQLTestCase


@unittest.skipUnless(DATABASE_URL, "set TEST_COMPETITION_DATABASE_URL for PostgreSQL tests")
class CompetitionApiTest(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        now = datetime.now(timezone.utc)
        signal = now.date()
        payload = self.seed_observation(
            signal, submission_open_at=now - timedelta(hours=1),
            deadline=now + timedelta(hours=1),
        )
        self.signal_date = signal.isoformat()
        self.client = TestClient(create_app(self.store, "admin-test"))
        self.auth = {"Authorization": "Bearer test-api-key"}
        self.payload = payload

    def tearDown(self):
        self.client.close()

    def decision(self, weights=None):
        return {
            "type": "decision_response",
            "protocol_version": "0.1",
            "run_id": "official_2026",
            "session_date": self.signal_date,
            "target_weights": weights or {"AAPL": 0.05},
        }

    def test_health_status_observation_and_raw_submission(self):
        self.assertEqual(self.client.get("/health").json(),
                         {"status": "ok", "database": "ok"})
        status_value = self.client.get("/api/v1/me/status", headers=self.auth)
        self.assertEqual(status_value.status_code, 200)
        self.assertTrue(status_value.json()["session"]["observation_available"])

        observation = self.client.get("/api/v1/me/observation", headers=self.auth)
        self.assertEqual(observation.status_code, 200)
        self.assertEqual(observation.json(), self.payload)

        raw_weights = {"AAPL": -0.3, "UNKNOWN": 9.0}
        response = self.client.post(
            "/api/v1/decisions", json=self.decision(raw_weights),
            headers=self.auth | {"Idempotency-Key": "request-1"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "RECEIVED")
        with psycopg.connect(DATABASE_URL) as connection:
            saved = connection.execute(
                "SELECT raw_payload_json FROM decision_submissions").fetchone()[0]
            self.assertEqual(saved["target_weights"], raw_weights)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM submission_weights").fetchone()[0], 0)

    def test_invalid_attempt_does_not_consume_slot(self):
        invalid = {"type": "decision_response"}
        response = self.client.post(
            "/api/v1/decisions", json=invalid,
            headers=self.auth | {"Idempotency-Key": "invalid"},
        )
        self.assertEqual(response.status_code, 422)
        valid = self.client.post(
            "/api/v1/decisions", json=self.decision(),
            headers=self.auth | {"Idempotency-Key": "valid"},
        )
        self.assertEqual(valid.status_code, 201)
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM submission_attempts").fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM decision_submissions").fetchone()[0], 1)

    def test_idempotency_http_statuses(self):
        headers = self.auth | {"Idempotency-Key": "same"}
        first = self.client.post("/api/v1/decisions", json=self.decision(), headers=headers)
        replay = self.client.post("/api/v1/decisions", json=self.decision(), headers=headers)
        conflict = self.client.post(
            "/api/v1/decisions", json=self.decision({"AAPL": 0.08}), headers=headers)
        second = self.client.post(
            "/api/v1/decisions", json=self.decision({"MSFT": 0.05}),
            headers=self.auth | {"Idempotency-Key": "different"},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(second.status_code, 409)

    def test_auth_body_limit_and_invalid_key(self):
        self.assertEqual(self.client.get(
            "/api/v1/me/status",
            headers={"Authorization": "Bearer wrong"}).status_code, 401)
        limited = TestClient(create_app(self.store, "admin-test", max_request_bytes=10))
        try:
            response = limited.post(
                "/api/v1/decisions", content=b'{"more":"than ten bytes"}',
                headers=self.auth | {"Idempotency-Key": "large",
                                     "Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 413)
        finally:
            limited.close()

    def test_admin_calendar_is_idempotent_and_conflicts_on_change(self):
        future = datetime.now(timezone.utc) + timedelta(days=10)
        body = {
            "trading_date": future.date().isoformat(),
            "market_open_at": future.isoformat(),
            "market_close_at": (future + timedelta(hours=6)).isoformat(),
            "submission_open_at": (future + timedelta(hours=7)).isoformat(),
            "submission_deadline_at": (future + timedelta(hours=20)).isoformat(),
        }
        headers = {"Authorization": "Bearer admin-test"}
        self.assertEqual(self.client.post(
            "/api/v1/admin/trading-days", json=body, headers=headers).status_code, 201)
        self.assertEqual(self.client.post(
            "/api/v1/admin/trading-days", json=body, headers=headers).status_code, 200)
        changed = dict(body, submission_deadline_at=(
            future + timedelta(hours=21)).isoformat())
        self.assertEqual(self.client.post(
            "/api/v1/admin/trading-days", json=changed, headers=headers).status_code, 409)


if __name__ == "__main__":
    unittest.main()
