from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from deployment.live_server.app import create_app
from deployment.live_server.store import LiveStore
from deployment.starter_kit.agent import Agent
from deployment.starter_kit.client import CompetitionClient, run_once


class TestClientAdapter(CompetitionClient):
    """Run the public Starter Kit against FastAPI without opening a network port."""

    def __init__(self, test_client: TestClient, api_key: str):
        self.test_client = test_client
        self.api_key = api_key

    def request(self, method: str, path: str, body=None, idempotency_key=None):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = self.test_client.request(method, path, json=body, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"server returned HTTP {response.status_code}: {response.json()}")
        return response.json()


class CompetitionApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LiveStore(Path(self.tmp.name) / "competition.sqlite3")
        self.key = self.store.register_team("team_001", "test-api-key")
        observation = {
            "session_date": "2026-10-27",
            "event_time_utc": "2099-10-27T20:00:00+00:00",
            "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "constraints": {"max_asset_weight": .10, "max_gross_exposure": 1.0,
                            "fee_rate": .001},
            "news": [],
        }
        market = {"AAPL": {"adj_open": 100.0, "adj_close": 110.0},
                  "MSFT": {"adj_open": 100.0, "adj_close": 100.0}}
        self.store.publish(observation, market)
        self.client = TestClient(create_app(self.store, "admin-test"))
        self.auth = {"Authorization": "Bearer test-api-key"}

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_openapi_and_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/api/v1/me/status", schema["paths"])
        self.assertIn("/api/v1/decisions", schema["paths"])

    def test_authenticated_observation_decision_state_and_leaderboard(self):
        team_status = self.client.get("/api/v1/me/status", headers=self.auth)
        self.assertEqual(team_status.status_code, 200)
        self.assertFalse(team_status.json()["session"]["decision_accepted"])

        observation = self.client.get("/api/v1/me/observation", headers=self.auth)
        self.assertEqual(observation.status_code, 200)
        self.assertEqual(observation.json()["portfolio"]["nav"], 1_000_000.0)

        decision = {"type": "decision_response", "protocol_version": "0.1",
                    "run_id": "official_2026", "session_date": "2026-10-27",
                    "target_weights": {"AAPL": .20}}
        headers = self.auth | {"Idempotency-Key": "request-1"}
        receipt = self.client.post("/api/v1/decisions", json=decision, headers=headers)
        self.assertEqual(receipt.status_code, 201)
        self.assertEqual(receipt.json()["sanitized_weights"]["AAPL"], .10)

        team_status = self.client.get("/api/v1/me/status", headers=self.auth).json()
        self.assertTrue(team_status["session"]["decision_accepted"])
        self.assertEqual(team_status["latest_submission"]["id"], "request-1")
        self.assertEqual(self.client.get("/api/v1/me/state", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/leaderboard").status_code, 200)

    def test_validation_errors_are_structured(self):
        self.assertEqual(self.client.get("/api/v1/me/state",
                                         headers={"Authorization": "Bearer wrong"}).status_code, 401)
        response = self.client.post("/api/v1/decisions", json={}, headers=self.auth)
        self.assertEqual(response.status_code, 422)  # Pydantic body validation happens first.
        malformed = {"type": "decision_response", "protocol_version": "bad",
                     "run_id": "r", "session_date": "not-a-date", "target_weights": {}}
        response = self.client.post("/api/v1/decisions", json=malformed,
                                    headers=self.auth | {"Idempotency-Key": "bad"})
        self.assertEqual(response.status_code, 422)

    def test_starter_kit_runs_one_complete_client_interaction(self):
        starter_client = TestClientAdapter(self.client, self.key)
        result = run_once(starter_client, Agent())
        self.assertEqual(result["action"], "submitted")
        self.assertTrue(result["receipt"]["accepted"])
        self.assertEqual(result["receipt"]["sanitized_weights"], {"AAPL": .05})

        repeated = run_once(starter_client, Agent())
        self.assertEqual(repeated["action"], "wait")
        self.assertEqual(repeated["reason"], "decision_already_accepted")


if __name__ == "__main__":
    unittest.main()

