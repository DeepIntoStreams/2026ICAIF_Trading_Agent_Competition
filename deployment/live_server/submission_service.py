"""Competition-owned processing of Deployment's raw submission handoff."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg.types.json import Jsonb

from .competition_adapter import CompetitionAdapter
from .store import canonical_json


VALIDATOR_VERSION = "reject-not-repair-1.0"


@dataclass(frozen=True)
class PreparedSubmission:
    submission_id: int
    source: str
    status: str
    target_weights: dict[int, Decimal] | None
    violations: list[str]

    @property
    def submitted(self) -> bool:
        return self.source == "PARTICIPANT"


class SubmissionService:
    def __init__(self, adapter: CompetitionAdapter):
        self.adapter = adapter

    def prepare(
        self, connection: Any, *, team_id: int, team_code: str,
        observation_id: int, signal_day_id: int, signal_date: date,
        execution_day_id: int, instruments: list[tuple[Any, ...]],
        constraints: dict[str, Any],
    ) -> PreparedSubmission:
        row = connection.execute(
            """SELECT id, source, status, raw_payload_json, expected_weight_count,
                      validation_summary_json
                 FROM decision_submissions
                WHERE team_id=%s AND execution_day_id=%s FOR UPDATE""",
            (team_id, execution_day_id),
        ).fetchone()
        if row is None:
            row = self._fallback(
                connection, team_id=team_id, team_code=team_code,
                observation_id=observation_id, signal_day_id=signal_day_id,
                signal_date=signal_date, execution_day_id=execution_day_id,
                instruments=instruments, constraints=constraints,
            )
            self._audit(connection, team_id, signal_day_id,
                        "FALLBACK_DECISION_CREATED", int(row[0]),
                        {"reason": "no_submission"})
        elif row[2] == "RECEIVED":
            row = self._validate(connection, row, instruments, constraints)
            event_type = "SUBMISSION_QUEUED" if row[2] == "QUEUED" else "SUBMISSION_REJECTED"
            self._audit(connection, team_id, signal_day_id, event_type, int(row[0]),
                        {"violations": list((row[5] or {}).get("violations") or [])})

        submission_id, source, status, _, expected_count, summary = row
        if status not in {"QUEUED", "REJECTED"}:
            raise RuntimeError(f"submission {submission_id} is not executable: {status}")
        if int(expected_count) != len(instruments):
            raise RuntimeError(
                f"submission {submission_id} expects {expected_count} weights; "
                f"the active universe contains {len(instruments)}"
            )
        violations = list((summary or {}).get("violations") or [])
        target = None
        if source == "PARTICIPANT" and status == "QUEUED":
            target = self._load_vector(
                connection, int(submission_id), [int(row[0]) for row in instruments],
            )
        return PreparedSubmission(int(submission_id), str(source), str(status),
                                  target, violations)

    def _validate(self, connection: Any, row: tuple[Any, ...],
                  instruments: list[tuple[Any, ...]],
                  constraints: dict[str, Any]) -> tuple[Any, ...]:
        submission_id, source, _, document, expected_count, _ = row
        raw_weights = document.get("target_weights") or {}
        verdict = self.adapter.validate(
            raw_weights, [str(instrument[1]) for instrument in instruments], constraints,
        )
        accepted = verdict["accepted"] or {}
        violations = list(verdict["violations"])
        status = "QUEUED" if verdict["ok"] else "REJECTED"
        timestamp = _now()
        for instrument_id, ticker, *_ in instruments:
            provided = ticker in raw_weights
            connection.execute(
                """INSERT INTO submission_weights
                       (submission_id, instrument_id, was_provided, raw_weight,
                        sanitized_weight, validation_codes_json, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (submission_id, instrument_id, provided,
                 _decimal_or_none(raw_weights.get(ticker)) if provided else None,
                 Decimal(str(accepted.get(ticker, 0))),
                 Jsonb(violations if provided and violations else []), timestamp),
            )
        summary = {"policy": "reject-not-repair", "violations": violations}
        gross = sum((Decimal(str(value)) for value in accepted.values()), Decimal("0"))
        connection.execute(
            """UPDATE decision_submissions
                  SET status=%s, rejection_reason=%s, validator_version=%s,
                      validation_policy_json=%s, validation_summary_json=%s,
                      stored_weight_count=%s, sanitized_gross_weight=%s,
                      weights_processed_at=%s, updated_at=%s
                WHERE id=%s""",
            (status, ",".join(violations) if violations else None,
             VALIDATOR_VERSION, Jsonb(constraints), Jsonb(summary),
             len(instruments), gross, timestamp, timestamp, submission_id),
        )
        return (submission_id, source, status, document, expected_count, summary)

    @staticmethod
    def _fallback(
        connection: Any, *, team_id: int, team_code: str,
        observation_id: int, signal_day_id: int, signal_date: date,
        execution_day_id: int, instruments: list[tuple[Any, ...]],
        constraints: dict[str, Any],
    ) -> tuple[Any, ...]:
        timestamp = _now()
        key = f"fallback:{signal_date.isoformat()}:{team_id}"
        document = {
            "type": "decision_response", "protocol_version": "0.1",
            "run_id": "official_2026", "team_id": team_code,
            "session_date": signal_date.isoformat(), "target_weights": {},
            "metadata": {"source": "deadline_fallback"},
        }
        payload_hash = hashlib.sha256(canonical_json(document).encode()).hexdigest()
        summary = {"policy": "hold", "violations": ["no_submission"]}
        submission_id = connection.execute(
            """INSERT INTO decision_submissions
                   (intake_attempt_id, team_id, observation_id, signal_day_id,
                    execution_day_id, idempotency_key, received_at, raw_payload_json,
                    payload_hash, source, status, validator_version,
                    validation_policy_json, validation_summary_json,
                    expected_weight_count, stored_weight_count, sanitized_gross_weight,
                    weights_processed_at, created_at, updated_at)
               VALUES (NULL,%s,%s,%s,%s,%s,%s,%s,%s,'FALLBACK','QUEUED',%s,%s,%s,
                       %s,%s,0,%s,%s,%s) RETURNING id""",
            (team_id, observation_id, signal_day_id, execution_day_id, key, timestamp,
             Jsonb(document), payload_hash, VALIDATOR_VERSION, Jsonb(constraints),
             Jsonb(summary), len(instruments), len(instruments), timestamp,
             timestamp, timestamp),
        ).fetchone()[0]
        for instrument_id, *_ in instruments:
            connection.execute(
                """INSERT INTO submission_weights
                       (submission_id, instrument_id, was_provided, raw_weight,
                        sanitized_weight, validation_codes_json, created_at)
                   VALUES (%s,%s,FALSE,NULL,0,'[]'::jsonb,%s)""",
                (submission_id, instrument_id, timestamp),
            )
        return (submission_id, "FALLBACK", "QUEUED", document,
                len(instruments), summary)

    @staticmethod
    def _load_vector(connection: Any, submission_id: int,
                     instrument_ids: list[int]) -> dict[int, Decimal]:
        rows = connection.execute(
            """SELECT instrument_id, sanitized_weight FROM submission_weights
                WHERE submission_id=%s ORDER BY instrument_id""",
            (submission_id,),
        ).fetchall()
        if [int(row[0]) for row in rows] != instrument_ids:
            raise RuntimeError(f"submission {submission_id} has an incomplete frozen vector")
        return {int(instrument_id): weight for instrument_id, weight in rows}

    @staticmethod
    def _audit(connection: Any, team_id: int, day_id: int, event_type: str,
               submission_id: int, details: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO audit_logs
                   (team_id, trading_day_id, actor_type, actor_id, event_type,
                    entity_type, entity_id, details_json, created_at)
               VALUES (%s,%s,'SYSTEM','submission_service',%s,
                       'decision_submission',%s,%s,%s)""",
            (team_id, day_id, event_type, submission_id, Jsonb(details), _now()),
        )


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)
