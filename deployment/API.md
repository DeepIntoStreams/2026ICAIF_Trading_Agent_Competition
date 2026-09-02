# Competition API quick start (interface draft v0.1)

This is the participant interface draft. The previous SQLite prototype implements
an older version for reference, but the HTTP service is temporarily disabled in
Compose until its persistence layer is migrated to the PostgreSQL contract.

## Authentication

API keys are issued by the organizer, not self-created by participants and not obtained from a
public endpoint. The operational flow is:

1. Organizer freezes a unique `team_id` for every approved team.
2. The organizer registration service creates the PostgreSQL team row and returns
   a plaintext key once. This service is the next persistence-layer increment.

3. Registration prints a high-entropy plaintext key exactly once. Only its SHA-256 hash is stored.
4. Organizer sends that key to the team through an authenticated private channel.
5. The team stores it in `COMPETITION_API_KEY`; it must not be committed to Git or placed in a URL.
6. Participant requests send it as a Bearer token. The server derives the team from the key, so
   no team ID is needed in the URL.

Example participant configuration:

```bash
export COMPETITION_BASE_URL='https://competition.example.org'
export COMPETITION_API_KEY='<key received privately from the organizer>'
python -m deployment.starter_kit.client
```

There is intentionally no `GET /api-key` endpoint: an unauthenticated API cannot safely decide
which team's secret to return. Rotation/revocation is a required next increment before production.

## Interactive API documentation

Once the FastAPI server is running:

```text
GET /docs          Swagger UI for reading and trying endpoints
GET /openapi.json  machine-readable API contract
GET /health        process health check
```

## Get the currently published observation

Participants normally poll status first:

```http
GET /api/v1/me/status
Authorization: Bearer <team-api-key>
```

It reports the server time, current session/deadline, whether an observation is available,
whether this team already has an accepted decision, the latest receipt, and a small portfolio
summary. The team then requests the data itself:

```http
GET /api/v1/me/observation
Authorization: Bearer <team-api-key>
```

The server returns the currently available official observation augmented with only that team's
portfolio state. "Publish" in organizer commands means load data into the server; it is not a
push to participants.

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

These currently use `COMPETITION_ADMIN_TOKEN`; they must move to scoped organizer credentials before
production.

```text
POST /api/v1/admin/sessions   publish immutable observation + hidden settlement market data
POST /api/v1/admin/settle     execute queued targets and update state
```

The next interface increment will introduce `/api/v1/runs` and a historical episode store so
Validation can rapidly repeat the same GET-observation/POST-decision loop. Official Competition
will use the same resources but wait for organizer-published real trading sessions.
