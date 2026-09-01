# Competition API quick start (interface draft v0.1)

This is the runnable subset of the unified protocol. `ARCHITECTURE.md` defines the target run
model that will add fast historical Validation episodes without changing the observation or
decision envelopes.

## Authentication

The organizer issues a team API key:

```bash
python -m deployment.live_server.manage --db /data/competition.sqlite3 register-team team_001
```

The command prints the plaintext once. Participant requests send it as a bearer token. The
server derives the team from this key; no team ID is needed in the URL.

## Get the currently published observation

```http
GET /api/v1/me/observation
Authorization: Bearer <team-api-key>
```

The shared official observation is augmented with only that team's portfolio state.

## Submit one decision

```http
POST /api/v1/decisions
Authorization: Bearer <team-api-key>
Idempotency-Key: <client-generated-unique-id>
Content-Type: application/json

{
  "type": "decision_response",
  "protocol_version": "0.1",
  "run_id": "official_2026",
  "session_date": "2026-10-27",
  "target_weights": {"AAPL": 0.10, "MSFT": 0.05},
  "metadata": {"agent_version": "example-1"}
}
```

`team_id` may be included for compatibility, but it must match the authenticated team. The
server receive time decides whether the submission is late. The current policy accepts the first
valid decision for a team/session; a repeated `Idempotency-Key` returns its original receipt.

Example receipt:

```json
{
  "accepted": true,
  "idempotent": false,
  "reason": null,
  "sanitized_weights": {"AAPL": 0.1, "MSFT": 0.05},
  "violations": []
}
```

## Read team state and local leaderboard

```http
GET /api/v1/me/state
Authorization: Bearer <team-api-key>

GET /api/v1/leaderboard
```

The leaderboard endpoint is local and derived from the organizer database. Codabench syncing is
not part of v0.1.

## Organizer-only prototype endpoints

These currently use `LIVE_ADMIN_TOKEN`; they must move to scoped organizer credentials before
production.

```text
POST /api/v1/admin/sessions   publish immutable observation + hidden settlement market data
POST /api/v1/admin/settle     execute queued targets and update state
```

The next interface increment will introduce `/api/v1/runs` and a historical episode store so
Validation can rapidly repeat the same GET-observation/POST-decision loop. Official Competition
will use the same resources but wait for organizer-published real trading sessions.

