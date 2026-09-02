"""SQLite persistence and deterministic next-open settlement."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from portfolio_agent.metrics import compute_metrics
from portfolio_agent.risk import sanitize_target_weights

INITIAL_CASH = 1_000_000.0


class LiveStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
          team_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          session_date TEXT PRIMARY KEY, deadline_utc TEXT NOT NULL,
          observation_json TEXT NOT NULL, market_json TEXT NOT NULL,
          published_at TEXT NOT NULL, settled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS submissions (
          source_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, session_date TEXT NOT NULL,
          received_at TEXT NOT NULL, raw_json TEXT NOT NULL, sanitized_json TEXT NOT NULL,
          violations_json TEXT NOT NULL, accepted INTEGER NOT NULL, reason TEXT,
          FOREIGN KEY(team_id) REFERENCES teams(team_id));
        CREATE INDEX IF NOT EXISTS submission_day ON submissions(team_id, session_date, received_at);
        CREATE TABLE IF NOT EXISTS states (
          team_id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
          FOREIGN KEY(team_id) REFERENCES teams(team_id));
        """)
        self.db.commit()

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def register_team(self, team_id: str, token: str | None = None) -> str:
        token = token or secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        state = {"cash": INITIAL_CASH, "shares": {}, "queued": {}, "previous_target": {},
                 "nav": [INITIAL_CASH], "total_cost": 0.0, "total_traded": 0.0,
                 "violation_days": 0, "decision_days": 0, "last_settled": None}
        with self.db:
            self.db.execute("INSERT INTO teams VALUES (?, ?, ?)", (team_id, self._hash(token), now))
            self.db.execute("INSERT INTO states VALUES (?, ?)", (team_id, json.dumps(state)))
        return token

    def authenticate(self, team_id: str, token: str) -> bool:
        row = self.db.execute("SELECT token_hash FROM teams WHERE team_id=?", (team_id,)).fetchone()
        return bool(row and secrets.compare_digest(row["token_hash"], self._hash(token)))

    def team_for_api_key(self, token: str) -> str | None:
        """Resolve identity from a bearer key; client-provided team IDs are not trusted."""
        if not token:
            return None
        digest = self._hash(token)
        row = self.db.execute("SELECT team_id, token_hash FROM teams WHERE token_hash=?",
                              (digest,)).fetchone()
        if not row or not secrets.compare_digest(row["token_hash"], digest):
            return None
        return str(row["team_id"])

    def publish(self, observation: dict[str, Any], market: dict[str, Any]) -> None:
        date = observation["session_date"]
        deadline = observation["event_time_utc"]
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, 0)",
                (date, deadline, json.dumps(observation), json.dumps(market), now),
            )

    def session(self, date: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM sessions WHERE session_date=?" if date else \
              "SELECT * FROM sessions ORDER BY session_date DESC LIMIT 1"
        row = self.db.execute(sql, (date,) if date else ()).fetchone()
        if not row:
            return None
        return {"session_date": row["session_date"], "deadline_utc": row["deadline_utc"],
                "observation": json.loads(row["observation_json"]),
                "market": json.loads(row["market_json"]), "settled": bool(row["settled"])}

    def state(self, team_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT state_json FROM states WHERE team_id=?", (team_id,)).fetchone()
        if not row:
            raise KeyError(team_id)
        return json.loads(row["state_json"])

    def team_observation(self, team_id: str, date: str | None = None) -> dict[str, Any] | None:
        session = self.session(date)
        if not session:
            return None
        obs, state = session["observation"], self.state(team_id)
        market = session["market"]
        px = {t: v.get("adj_open") for t, v in market.items() if v.get("adj_open")}
        nav = state["cash"] + sum(sh * px[t] for t, sh in state["shares"].items() if t in px)
        obs["portfolio"] = {
            "weights": {t: sh * px[t] / nav for t, sh in state["shares"].items() if t in px and nav},
            "cash_ratio": state["cash"] / nav if nav else 1.0, "nav": nav,
        }
        return obs

    def team_status(self, team_id: str) -> dict[str, Any]:
        """Return the small polling payload a participant needs before requesting data."""
        session = self.session()
        state = self.state(team_id)
        submission = None
        if session:
            row = self.db.execute(
                "SELECT source_id, received_at, accepted, reason FROM submissions "
                "WHERE team_id=? AND session_date=? ORDER BY received_at DESC LIMIT 1",
                (team_id, session["session_date"]),
            ).fetchone()
            if row:
                submission = {
                    "id": row["source_id"], "received_at": row["received_at"],
                    "accepted": bool(row["accepted"]), "reason": row["reason"],
                }
        return {
            "team_id": team_id,
            "server_time_utc": datetime.now(timezone.utc).isoformat(),
            "session": None if not session else {
                "session_date": session["session_date"],
                "deadline_utc": session["deadline_utc"],
                "settled": session["settled"],
                "observation_available": True,
                "decision_accepted": bool(submission and submission["accepted"]),
            },
            "latest_submission": submission,
            "portfolio": {
                "nav": state["nav"][-1],
                "last_settled": state["last_settled"],
            },
        }

    def submit(self, source_id: str, authenticated_team: str, document: dict[str, Any],
               received_at: str | None = None) -> dict[str, Any]:
        existing = self.db.execute("SELECT * FROM submissions WHERE source_id=?", (source_id,)).fetchone()
        if existing:
            return {"accepted": bool(existing["accepted"]), "idempotent": True,
                    "reason": existing["reason"]}
        date = document.get("session_date", "")
        session = self.session(date)
        received = received_at or datetime.now(timezone.utc).isoformat()
        reason = None
        prior = self.db.execute(
            "SELECT source_id FROM submissions WHERE team_id=? AND session_date=? AND accepted=1",
            (authenticated_team, date),
        ).fetchone()
        if document.get("type") != "decision_response": reason = "invalid_type"
        elif document.get("team_id") not in (None, authenticated_team): reason = "team_mismatch"
        elif not session: reason = "unknown_session"
        elif datetime.fromisoformat(received.replace("Z", "+00:00")) > datetime.fromisoformat(
                session["deadline_utc"].replace("Z", "+00:00")): reason = "late_submission"
        elif prior: reason = "decision_already_accepted"
        elif not isinstance(document.get("target_weights"), dict): reason = "invalid_weights"
        tickers = [a["ticker"] for a in session["observation"]["assets"]] if session else []
        constraints = session["observation"].get("constraints", {}) if session else {}
        clean, violations = sanitize_target_weights(
            document.get("target_weights", {}) if not reason else {}, tickers,
            float(constraints.get("max_asset_weight", .10)),
            float(constraints.get("max_gross_exposure", 1.0)))
        accepted = not reason
        with self.db:
            self.db.execute("INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (source_id, authenticated_team, date, received, json.dumps(document),
                             json.dumps(clean), json.dumps(violations), int(accepted), reason))
        return {"accepted": accepted, "idempotent": False, "reason": reason,
                "sanitized_weights": clean, "violations": violations}

    def _latest_weights(self, team: str, date: str, previous: dict[str, float]):
        row = self.db.execute(
            "SELECT * FROM submissions WHERE team_id=? AND session_date=? AND accepted=1 "
            "ORDER BY received_at DESC LIMIT 1", (team, date)).fetchone()
        if not row:
            return previous, ["no_submission"]
        return json.loads(row["sanitized_json"]), json.loads(row["violations_json"])

    def settle(self, date: str) -> list[dict[str, Any]]:
        session = self.session(date)
        if not session or session["settled"]:
            raise ValueError("session missing or already settled")
        px = {t: v for t, v in session["market"].items() if v.get("adj_open") and v.get("adj_close")}
        fee_rate = float(session["observation"].get("constraints", {}).get("fee_rate", .001))
        results = []
        teams = [r["team_id"] for r in self.db.execute("SELECT team_id FROM teams")]
        for team in teams:
            st = self.state(team)
            queued = st.get("queued", {})
            if queued:
                nav_open = st["cash"] + sum(sh * px[t]["adj_open"] for t, sh in st["shares"].items() if t in px)
                new = {t: 0.0 for t in st["shares"]}
                for t, weight in queued.items():
                    if t in px: new[t] = weight * nav_open / px[t]["adj_open"]
                traded = sum(abs(new.get(t, 0) - st["shares"].get(t, 0)) * px[t]["adj_open"]
                             for t in set(new) | set(st["shares"]) if t in px)
                fee = traded * fee_rate
                invested = sum(sh * px[t]["adj_open"] for t, sh in new.items() if t in px)
                st.update(cash=nav_open - invested - fee, shares=new,
                          total_cost=st["total_cost"] + fee,
                          total_traded=st["total_traded"] + traded)
            target, violations = self._latest_weights(team, date, st["previous_target"])
            st["queued"], st["previous_target"] = target, target
            st["decision_days"] += 1
            if violations: st["violation_days"] += 1
            nav_close = st["cash"] + sum(sh * px[t]["adj_close"] for t, sh in st["shares"].items() if t in px)
            st["nav"].append(nav_close); st["last_settled"] = date
            with self.db:
                self.db.execute("UPDATE states SET state_json=? WHERE team_id=?", (json.dumps(st), team))
            results.append({"team_id": team, "nav": nav_close, "violations": violations})
        with self.db:
            self.db.execute("UPDATE sessions SET settled=1 WHERE session_date=?", (date,))
        return results

    def leaderboard(self) -> list[dict[str, Any]]:
        board = []
        for row in self.db.execute("SELECT team_id, state_json FROM states"):
            st = json.loads(row["state_json"])
            metrics = None
            if len(st["nav"]) >= 2:
                metrics = compute_metrics(st["nav"], initial_capital=INITIAL_CASH,
                                          total_transaction_cost=st["total_cost"],
                                          total_trade_value=st["total_traded"],
                                          violation_steps=st["violation_days"],
                                          decision_steps=st["decision_days"])
            board.append({"team_id": row["team_id"], "metrics": metrics,
                          "nav": st["nav"][-1],
                          "status": "ranked" if metrics is not None else "pending"})
        board.sort(key=lambda x: (
            x["metrics"] is not None,
            x["metrics"]["m1_cumulative_return"] if x["metrics"] else float("-inf"),
        ), reverse=True)
        return board
