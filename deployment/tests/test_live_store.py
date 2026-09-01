from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deployment.live_server.store import LiveStore


def observation(date="2026-10-27"):
    return {"session_date": date, "event_time_utc": f"{date}T20:00:00+00:00",
            "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "constraints": {"max_asset_weight": .10, "max_gross_exposure": 1.0,
                            "fee_rate": .001}, "news": []}


def market():
    return {"AAPL": {"adj_open": 100.0, "adj_close": 110.0},
            "MSFT": {"adj_open": 100.0, "adj_close": 100.0}}


class LiveStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LiveStore(Path(self.tmp.name) / "live.sqlite")
        self.token = self.store.register_team("team")
        self.store.publish(observation(), market())

    def tearDown(self): self.tmp.cleanup()

    def test_identity_deadline_and_idempotency(self):
        doc = {"type": "decision_response", "team_id": "team", "session_date": "2026-10-27",
               "target_weights": {"AAPL": .2}}
        result = self.store.submit("cb-1", "team", doc, "2026-10-27T19:00:00+00:00")
        self.assertTrue(result["accepted"]); self.assertEqual(result["sanitized_weights"]["AAPL"], .1)
        self.assertTrue(self.store.submit("cb-1", "team", doc)["idempotent"])
        wrong = dict(doc, team_id="other")
        self.assertEqual(self.store.submit("cb-2", "team", wrong)["reason"], "team_mismatch")
        self.assertEqual(self.store.submit("cb-3", "team", doc, "2026-10-27T21:00:00+00:00")["reason"],
                         "late_submission")

    def test_api_key_resolves_identity_and_only_one_decision_is_accepted(self):
        self.assertEqual(self.store.team_for_api_key(self.token), "team")
        self.assertIsNone(self.store.team_for_api_key("wrong"))
        first = {"type": "decision_response", "session_date": "2026-10-27",
                 "target_weights": {"AAPL": .05}}
        self.assertTrue(self.store.submit("request-1", "team", first,
                                          "2026-10-27T19:00:00+00:00")["accepted"])
        second = dict(first, target_weights={"AAPL": .08})
        result = self.store.submit("request-2", "team", second,
                                   "2026-10-27T19:30:00+00:00")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "decision_already_accepted")

    def test_next_open_and_omitted_asset_liquidation(self):
        first = {"type": "decision_response", "team_id": "team", "session_date": "2026-10-27",
                 "target_weights": {"AAPL": .1}}
        self.store.submit("one", "team", first, "2026-10-27T19:00:00+00:00")
        self.store.settle("2026-10-27")             # queues; no same-day execution
        self.store.publish(observation("2026-10-28"), market())
        second = dict(first, session_date="2026-10-28", target_weights={"MSFT": .1})
        self.store.submit("two", "team", second, "2026-10-28T19:00:00+00:00")
        self.store.settle("2026-10-28")             # executes AAPL at the next open
        self.assertGreater(self.store.state("team")["shares"].get("AAPL", 0), 0)
        self.store.publish(observation("2026-10-29"), market())
        self.store.settle("2026-10-29")             # executes MSFT target, liquidates AAPL
        self.assertEqual(self.store.state("team")["shares"].get("AAPL"), 0.0)


if __name__ == "__main__": unittest.main()
