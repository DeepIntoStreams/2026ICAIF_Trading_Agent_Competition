"""PostgreSQL persistence for the participant-facing competition receiver."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import date, datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


class CompetitionStore:
    """Synchronous repository/service boundary used by FastAPI and the admin CLI."""

    def __init__(self, database_url: str):
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("a PostgreSQL COMPETITION_DATABASE_URL is required")
        self.database_url = database_url

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    @staticmethod
    def _hash_api_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _payload_hash(document: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()

    def health(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
        except psycopg.Error:
            return False

    def close(self) -> None:
        """Compatibility hook for a future pooled implementation."""

    def register_team(self, team_code: str, display_name: str | None = None,
                      token: str | None = None) -> str:
        token = token or secrets.token_urlsafe(32)
        timestamp = now_utc()
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO teams
                       (team_code, display_name, api_key_hash, status, created_at, updated_at)
                   VALUES (%s, %s, %s, 'ACTIVE', %s, %s)
                   RETURNING id""",
                (team_code, display_name or team_code, self._hash_api_key(token),
                 timestamp, timestamp),
            ).fetchone()
            self._audit(connection, row["id"], None, "ADMIN", "organizer",
                        "TEAM_CREATED", "team", row["id"], None,
                        {"team_code": team_code})
        return token

    def team_for_api_key(self, token: str) -> str | None:
        if not token:
            return None
        digest = self._hash_api_key(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT team_code, api_key_hash FROM teams "
                "WHERE api_key_hash=%s AND status='ACTIVE'", (digest,),
            ).fetchone()
        if not row or not secrets.compare_digest(row["api_key_hash"], digest):
            return None
        return str(row["team_code"])

    def create_trading_day(self, trading_date: date, market_open_at: datetime,
                           market_close_at: datetime, submission_open_at: datetime,
                           submission_deadline_at: datetime) -> tuple[dict[str, Any], bool]:
        values = (trading_date, market_open_at, market_close_at,
                  submission_open_at, submission_deadline_at)
        if not (market_open_at < market_close_at and
                submission_open_at < submission_deadline_at):
            raise ValueError("market and submission windows must be strictly ordered")
        timestamp = now_utc()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trading_days WHERE trading_date=%s FOR UPDATE", (trading_date,),
            ).fetchone()
            if row:
                existing = tuple(row[key] for key in (
                    "trading_date", "market_open_at", "market_close_at",
                    "submission_open_at", "submission_deadline_at"))
                if existing != values:
                    raise ValueError("trading day already exists with different times")
                return dict(row), True
            row = connection.execute(
                """INSERT INTO trading_days
                       (trading_date, market_open_at, market_close_at,
                        submission_open_at, submission_deadline_at, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (*values, timestamp, timestamp),
            ).fetchone()
            self._audit(connection, None, row["id"], "ADMIN", "calendar",
                        "TRADING_DAY_CREATED", "trading_day", row["id"], None,
                        {"trading_date": trading_date.isoformat()})
            return dict(row), False

    def _team(self, connection, team_code: str, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        return connection.execute(
            "SELECT id, team_code, status FROM teams WHERE team_code=%s" + suffix,
            (team_code,),
        ).fetchone()

    @staticmethod
    def _audit(connection, team_id: int | None, day_id: int | None,
               actor_type: str, actor_id: str | None, event_type: str,
               entity_type: str | None, entity_id: int | None,
               request_id: str | None, details: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO audit_logs
                   (team_id, trading_day_id, actor_type, actor_id, event_type,
                    entity_type, entity_id, request_id, details_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (team_id, day_id, actor_type, actor_id, event_type, entity_type,
             entity_id, request_id, Jsonb(details), now_utc()),
        )

    @staticmethod
    def _current_observation(connection, team_id: int, session_date: date | None = None,
                             *, lock: bool = False):
        conditions = ["o.team_id=%s", "o.published_at IS NOT NULL"]
        params: list[Any] = [team_id]
        if session_date is not None:
            conditions.append("td.trading_date=%s")
            params.append(session_date)
        lock_clause = " FOR UPDATE OF o" if lock else ""
        return connection.execute(
            """SELECT o.*, td.trading_date, td.submission_open_at,
                      td.submission_deadline_at
                 FROM observations o
                 JOIN trading_days td ON td.id=o.trading_day_id
                WHERE """ + " AND ".join(conditions) +
            " ORDER BY td.trading_date DESC LIMIT 1" + lock_clause,
            tuple(params),
        ).fetchone()

    def team_status(self, team_code: str, at: datetime | None = None) -> dict[str, Any]:
        at = at or now_utc()
        with self._connect() as connection:
            team = self._team(connection, team_code)
            if not team:
                raise KeyError(team_code)
            observation = self._current_observation(connection, team["id"])
            submission = None
            if observation:
                submission = connection.execute(
                    """SELECT id, idempotency_key, received_at, status
                         FROM decision_submissions
                        WHERE team_id=%s AND signal_day_id=%s""",
                    (team["id"], observation["trading_day_id"]),
                ).fetchone()
        if not observation:
            session = None
        else:
            opens = observation["submission_open_at"]
            deadline = observation["submission_deadline_at"]
            session = {
                "session_date": observation["trading_date"].isoformat(),
                "submission_open_at": opens.isoformat() if opens else None,
                "submission_deadline_at": deadline.isoformat() if deadline else None,
                "observation_available": bool(opens and deadline and opens <= at <= deadline),
                "decision_accepted": submission is not None,
            }
        return {
            "team_id": team_code,
            "server_time_utc": at.isoformat(),
            "session": session,
            "latest_submission": None if not submission else {
                "id": submission["id"],
                "idempotency_key": submission["idempotency_key"],
                "received_at": submission["received_at"].isoformat(),
                "status": submission["status"],
                "accepted": True,
            },
        }

    def team_observation(self, team_code: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            team = self._team(connection, team_code)
            if not team:
                raise KeyError(team_code)
            observation = self._current_observation(connection, team["id"], lock=True)
            if not observation:
                return None
            if observation["first_served_at"] is None:
                served_at = now_utc()
                connection.execute(
                    "UPDATE observations SET first_served_at=%s WHERE id=%s",
                    (served_at, observation["id"]),
                )
                self._audit(connection, team["id"], observation["trading_day_id"],
                            "TEAM", team_code, "OBSERVATION_FIRST_SERVED", "observation",
                            observation["id"], None, {})
            return dict(observation["payload_json"])

    def record_invalid_attempt(self, team_code: str, request_id: str,
                               idempotency_key: str | None, received_at: datetime,
                               document: dict[str, Any] | None, payload_hash: str | None,
                               reason: str) -> None:
        session_date = None
        if document and isinstance(document.get("session_date"), str):
            try:
                session_date = date.fromisoformat(document["session_date"])
            except ValueError:
                pass
        with self._connect() as connection:
            team = self._team(connection, team_code)
            if not team:
                return
            day = None
            if session_date:
                day = connection.execute(
                    "SELECT id FROM trading_days WHERE trading_date=%s", (session_date,),
                ).fetchone()
            attempt_id = self._insert_attempt(
                connection, team["id"], day["id"] if day else None, request_id,
                idempotency_key, received_at, document, payload_hash,
                "INVALID_REQUEST", reason,
                {"hash_kind": "raw_body_sha256"} if payload_hash else {},
            )
            self._audit(connection, team["id"], day["id"] if day else None,
                        "TEAM", team_code, "SUBMISSION_REJECTED", "submission_attempt",
                        attempt_id, request_id, {"reason": reason})

    @staticmethod
    def _insert_attempt(connection, team_id: int, day_id: int | None, request_id: str,
                        idempotency_key: str | None, received_at: datetime,
                        document: dict[str, Any] | None, payload_hash: str | None,
                        outcome: str, reason: str | None,
                        details: dict[str, Any] | None = None) -> int:
        row = connection.execute(
            """INSERT INTO submission_attempts
                   (team_id, trading_day_id, request_id, idempotency_key, received_at,
                    payload_json, payload_hash, outcome, rejection_reason,
                    details_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (team_id, day_id, request_id, idempotency_key, received_at,
             Jsonb(document) if document is not None else None, payload_hash,
             outcome, reason, Jsonb(details or {}), now_utc()),
        ).fetchone()
        return int(row["id"])

    def submit(self, team_code: str, document: dict[str, Any], idempotency_key: str,
               received_at: datetime | None = None, request_id: str | None = None) -> dict[str, Any]:
        received_at = received_at or now_utc()
        request_id = request_id or str(uuid.uuid4())
        payload_hash = self._payload_hash(document)
        session_date = date.fromisoformat(document["session_date"])

        with self._connect() as connection:
            team = self._team(connection, team_code)
            if not team or team["status"] != "ACTIVE":
                raise KeyError(team_code)
            observation = self._current_observation(
                connection, team["id"], session_date, lock=True)
            day_id = observation["trading_day_id"] if observation else None

            existing_key = connection.execute(
                """SELECT id, received_at, payload_hash, status
                     FROM decision_submissions
                    WHERE team_id=%s AND idempotency_key=%s""",
                (team["id"], idempotency_key),
            ).fetchone()
            if existing_key:
                same = secrets.compare_digest(existing_key["payload_hash"], payload_hash)
                outcome = "IDEMPOTENT_REPLAY" if same else "IDEMPOTENCY_CONFLICT"
                reason = "same_request_replayed" if same else "idempotency_key_reused"
                attempt_id = self._insert_attempt(
                    connection, team["id"], day_id, request_id, idempotency_key,
                    received_at, document, payload_hash, outcome, reason,
                    {"submission_id": existing_key["id"],
                     "hash_kind": "canonical_json_sha256"},
                )
                self._audit(connection, team["id"], day_id, "TEAM", team_code,
                            outcome, "submission_attempt", attempt_id, request_id,
                            {"submission_id": existing_key["id"]})
                if not same:
                    return {"accepted": False, "idempotent": False,
                            "reason": "idempotency_key_reused"}
                return self._receipt(existing_key, idempotent=True)

            outcome = reason = None
            if document.get("team_id") not in (None, team_code):
                outcome, reason = "TEAM_MISMATCH", "team_mismatch"
            elif not observation:
                outcome, reason = "UNKNOWN_SESSION", "unknown_or_unpublished_session"
            elif not observation["submission_open_at"] or not observation["submission_deadline_at"]:
                outcome, reason = "OUTSIDE_WINDOW", "submission_window_not_configured"
            elif not (observation["submission_open_at"] <= received_at <=
                      observation["submission_deadline_at"]):
                outcome, reason = "OUTSIDE_WINDOW", "outside_submission_window"

            next_day = None
            if observation and not outcome:
                next_day = connection.execute(
                    """SELECT id, trading_date, market_open_at FROM trading_days
                        WHERE trading_date>%s ORDER BY trading_date LIMIT 1""",
                    (session_date,),
                ).fetchone()
                if not next_day:
                    outcome, reason = "UNKNOWN_SESSION", "next_trading_day_not_configured"

            if observation and not outcome:
                existing_day = connection.execute(
                    """SELECT id FROM decision_submissions
                        WHERE team_id=%s AND signal_day_id=%s""",
                    (team["id"], day_id),
                ).fetchone()
                if existing_day:
                    outcome, reason = "ALREADY_SUBMITTED", "decision_already_accepted"

            if outcome:
                attempt_id = self._insert_attempt(
                    connection, team["id"], day_id, request_id, idempotency_key,
                    received_at, document, payload_hash, outcome, reason,
                    {"hash_kind": "canonical_json_sha256"},
                )
                self._audit(connection, team["id"], day_id, "TEAM", team_code,
                            "SUBMISSION_REJECTED", "submission_attempt", attempt_id,
                            request_id, {"reason": reason})
                return {"accepted": False, "idempotent": False, "reason": reason}

            attempt_id = self._insert_attempt(
                connection, team["id"], day_id, request_id, idempotency_key,
                received_at, document, payload_hash, "ACCEPTED", None,
                {"hash_kind": "canonical_json_sha256"},
            )
            assets = observation["payload_json"].get("assets", [])
            expected_count = len({asset.get("ticker") for asset in assets
                                  if isinstance(asset, dict) and asset.get("ticker")})
            agent_version = (document.get("metadata") or {}).get("agent_version")
            timestamp = now_utc()
            submission = connection.execute(
                """INSERT INTO decision_submissions
                       (intake_attempt_id, team_id, observation_id, signal_day_id,
                        execution_day_id, idempotency_key, received_at, raw_payload_json,
                        payload_hash, source, status, validation_summary_json,
                        expected_weight_count, stored_weight_count, agent_version,
                        created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PARTICIPANT',
                           'RECEIVED', '{}'::jsonb, %s, 0, %s, %s, %s)
                   RETURNING id, received_at, payload_hash, status""",
                (attempt_id, team["id"], observation["id"], day_id, next_day["id"],
                 idempotency_key, received_at, Jsonb(document), payload_hash,
                 expected_count, agent_version, timestamp, timestamp),
            ).fetchone()
            self._audit(connection, team["id"], day_id, "TEAM", team_code,
                        "SUBMISSION_RECEIVED", "decision_submission", submission["id"],
                        request_id, {"execution_day": next_day["trading_date"].isoformat()})
            return self._receipt(submission, idempotent=False)

    @staticmethod
    def _receipt(row: dict[str, Any], *, idempotent: bool) -> dict[str, Any]:
        return {
            "accepted": True,
            "id": row["id"],
            "status": row["status"],
            "received_at": row["received_at"].isoformat(),
            "idempotent": idempotent,
            "reason": None,
        }


LiveStore = CompetitionStore
