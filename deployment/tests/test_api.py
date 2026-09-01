from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from deployment.live_server.app import handler_for
from deployment.live_server.store import LiveStore


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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.store, "admin-test"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method: str, path: str, body=None, key="test-api-key", idem=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {key}"}
        if data is not None: headers["Content-Type"] = "application/json"
        if idem: headers["Idempotency-Key"] = idem
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_authenticated_observation_decision_state_and_leaderboard(self):
        status, observation = self.request("GET", "/api/v1/me/observation")
        self.assertEqual(status, 200)
        self.assertEqual(observation["portfolio"]["nav"], 1_000_000.0)

        decision = {"type": "decision_response", "protocol_version": "0.1",
                    "run_id": "official_2026", "session_date": "2026-10-27",
                    "target_weights": {"AAPL": .20}}
        status, receipt = self.request("POST", "/api/v1/decisions", decision, idem="request-1")
        self.assertEqual(status, 201)
        self.assertEqual(receipt["sanitized_weights"]["AAPL"], .10)
        self.assertTrue(receipt["violations"])

        status, state = self.request("GET", "/api/v1/me/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["nav"], [1_000_000.0])

        status, board = self.request("GET", "/api/v1/leaderboard")
        self.assertEqual(status, 200)
        self.assertEqual(board[0]["team_id"], "team_001")
        self.assertEqual(board[0]["status"], "pending")

    def test_bad_key_and_missing_idempotency_key_are_rejected(self):
        status, _ = self.request("GET", "/api/v1/me/state", key="wrong")
        self.assertEqual(status, 401)
        status, body = self.request("POST", "/api/v1/decisions", {}, idem=None)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "missing_idempotency_key")


if __name__ == "__main__":
    unittest.main()
