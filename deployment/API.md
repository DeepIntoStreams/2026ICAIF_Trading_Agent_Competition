# Competition receiver API (v0.2)

This is the PostgreSQL-backed participant intake interface. Weight sanitation,
execution, portfolio settlement, and leaderboard calculation are owned by the
Competition component and are not HTTP receiver responsibilities.

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
  "id": 200,
  "status": "RECEIVED",
  "received_at": "2026-10-27T19:00:00+00:00",
  "idempotent": false,
  "reason": null
}
```

The receipt never contains sanitized weights. Semantically invalid weights such
as unknown tickers, negative values, or cap violations remain unchanged in the
raw database record for Competition to process. Structurally invalid requests,
late requests, duplicates, and idempotency conflicts are recorded as attempts
but do not create another official submission.

## Organizer endpoints

These use `COMPETITION_ADMIN_TOKEN`:

```text
POST /api/v1/admin/teams
POST /api/v1/admin/trading-days
```

Trading-day timestamps must include a timezone. Repeating an identical calendar
record is idempotent; changing an existing date returns a conflict. Observations
are written by the Data/Competition workflow after their referenced portfolio
snapshots exist.
