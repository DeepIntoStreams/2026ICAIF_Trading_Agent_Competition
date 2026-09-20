#!/usr/bin/env python3
"""Call the running receiver and assert its PostgreSQL intake behavior."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import psycopg


BASE_URL = os.environ.get("COMPETITION_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
DATABASE_URL = os.environ.get("TEST_COMPETITION_DATABASE_URL", "")
API_KEY = os.environ.get("COMPETITION_API_KEY", "receiver-manual-test-api-key")


def call(method: str, path: str, body=None, key: str | None = None):
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    request = urllib.request.Request(BASE_URL + path, data=payload, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def check(name: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    print(f"[PASS] {name}: {actual!r}")


def main() -> int:
    if not DATABASE_URL:
        raise SystemExit("set TEST_COMPETITION_DATABASE_URL")

    status_code, status_body = call("GET", "/api/v1/me/status")
    check("status endpoint", status_code, 200)
    check("observation available", status_body["session"]["observation_available"], True)
    session_date = status_body["session"]["session_date"]

    code, observation = call("GET", "/api/v1/me/observation")
    check("observation endpoint", code, 200)
    check("observation session", observation["session_date"], session_date)

    code, _ = call("POST", "/api/v1/decisions",
                   {"type": "decision_response"}, "invalid-envelope")
    check("invalid envelope rejected", code, 422)

    decision = {
        "type": "decision_response",
        "protocol_version": "0.1",
        "run_id": "official_2026",
        "session_date": session_date,
        "target_weights": {"AAPL": -0.25, "UNKNOWN": 3.0},
        "metadata": {"agent_version": "manual-case-1"},
    }
    code, accepted = call("POST", "/api/v1/decisions", decision, "manual-request-1")
    check("first valid request", code, 201)
    check("handoff status", accepted["status"], "RECEIVED")

    code, replay = call("POST", "/api/v1/decisions", decision, "manual-request-1")
    check("same request replay", code, 200)
    check("same submission id", replay["id"], accepted["id"])

    changed = dict(decision, target_weights={"AAPL": 0.08})
    code, _ = call("POST", "/api/v1/decisions", changed, "manual-request-1")
    check("idempotency conflict", code, 409)

    code, _ = call("POST", "/api/v1/decisions", changed, "manual-request-2")
    check("second daily submission", code, 409)

    with psycopg.connect(DATABASE_URL) as connection:
        raw, state = connection.execute(
            "SELECT raw_payload_json, status FROM decision_submissions"
        ).fetchone()
        attempts = connection.execute(
            "SELECT outcome FROM submission_attempts ORDER BY id"
        ).fetchall()
        weight_count = connection.execute(
            "SELECT count(*) FROM submission_weights"
        ).fetchone()[0]
        execution_count = connection.execute(
            "SELECT count(*) FROM executions"
        ).fetchone()[0]

    check("raw weights unchanged", raw["target_weights"], decision["target_weights"])
    check("database handoff state", state, "RECEIVED")
    check("attempt trail", [row[0] for row in attempts], [
        "INVALID_REQUEST", "ACCEPTED", "IDEMPOTENT_REPLAY",
        "IDEMPOTENCY_CONFLICT", "ALREADY_SUBMITTED",
    ])
    check("server did not sanitize", weight_count, 0)
    check("server did not execute", execution_count, 0)
    print("\nAll receiver cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
