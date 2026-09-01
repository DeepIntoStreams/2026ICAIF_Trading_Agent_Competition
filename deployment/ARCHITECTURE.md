# Unified competition-server architecture (draft v0.1)

This document records the deployment contract derived from
`Validation_and_Official_Competition_Interaction_Logic.pdf`. It is the working design for the
server and Starter Kit; unresolved policy choices are called out instead of being embedded in
code.

## 1. System boundary

Participants always run their agents locally. The organizer operates one competition service
for both phases. Codabench is not in the observation/decision/settlement path.

```text
participant agent + official client
              |
              | team API key
              v
       competition server  --------> SQLite (development) / PostgreSQL (production)
              |                         observations, decisions, states, audit, leaderboard
              +---- validation: historical clock advances immediately
              +---- official: real NYSE clock advances once per trading day

Codabench (separate): final code archive intake; optional leaderboard mirror later
```

The server database and immutable settlement records are authoritative. Any Codabench
leaderboard is a derived mirror and must never be used to reconstruct portfolio state.

The current Python package remains under `deployment/live_server` to avoid a premature rename,
but it now represents the unified competition server. Rename it only after the API contract is
stable.

## 2. One protocol, two clocks

Both phases use the same authentication, observation, decision, constraint, next-open,
transaction-cost, and metric code. A run has a configured phase and clock policy.

| Property | Validation | Official competition |
| --- | --- | --- |
| Data | fixed historical episode | organizer-published live sessions |
| Clock | next observation becomes available immediately after a decision/settlement | next observation becomes available on the next real trading day |
| Run creation | participant starts an attempt | organizer creates/activates the official run |
| Limit | up to 10 complete attempts per team per server day (configurable) | one accepted decision per team/session |
| Result | returned at end of episode; unofficial | accumulated official leaderboard |
| Protocol | identical | identical |

The official client should only need a different base URL or phase setting. Agent code must not
branch on the phase.

## 3. Observation and execution invariants

For decision session `t`, the observation contains:

- completed daily market history ending at `t-1`;
- `current_open` for `t` after it becomes available;
- point-in-time fundamentals;
- the authenticated team's current weights, cash, NAV, and drawdown;
- the same constraints and protocol version in both phases;
- no organizer-provided news. Participants may obtain eligible public news themselves.

It must not contain the full OHLCV for `t`. A decision made in session `t` is queued and executed
at the next trading session's open. The last decision is followed by one final next-open
settlement without requesting another decision.

`event_time_utc` should mean the server submission deadline, not the observation generation
time. The server receive timestamp is authoritative for lateness.

## 4. Authentication draft

Each team receives a random API key with at least 256 bits of entropy. The plaintext is shown
only when issued or rotated; only a hash is stored.

Requests use:

```http
Authorization: Bearer <team-api-key>
```

The API key determines the team. A client-supplied `team_id` is optional protocol metadata and,
if present, must match the authenticated team. It is never authentication.

Production requirements:

- HTTPS only; never place keys in query strings or logs;
- separate organizer credentials from team API keys;
- key rotation and revocation with an audit record;
- rate limiting by key and IP, bounded request bodies, and replay/idempotency protection;
- secrets stored in a managed secret store; database backups encrypted;
- return the same generic 401 response for unknown and revoked keys.

## 5. HTTP interface draft

The unified participant workflow is deliberately small:

```text
POST /api/v1/runs
GET  /api/v1/runs/{run_id}/observation
POST /api/v1/runs/{run_id}/decisions
GET  /api/v1/runs/{run_id}/results
GET  /api/v1/leaderboard
```

All participant endpoints require the Bearer API key. `POST /runs` accepts
`{"phase":"validation"}` for a new validation attempt. For `official`, it returns the team's
organizer-provisioned persistent run; participants cannot create extra official runs.

Decision requests use the existing `decision_response` envelope. Clients should send an
`Idempotency-Key` header. Repeating the same key returns the original receipt; reusing it with a
different body is an error.

Expected run states are:

```text
created -> awaiting_decision -> advancing -> awaiting_decision -> ... -> final_settlement -> complete
```

In validation, `advancing` happens synchronously or as a short background job. In official mode,
it waits for the organizer to publish and settle the next real session. Consequently the client
sees the same resources and schemas but a different `next_observation_available` time.

Administrative operations remain separate and require an organizer credential:

```text
POST /api/v1/admin/teams
POST /api/v1/admin/sessions
POST /api/v1/admin/settle
POST /api/v1/admin/official-runs
GET  /api/v1/admin/audit/...
```

## 6. Persistence model

The target model separates definitions from per-team mutable state:

- `teams`, `api_credentials`: identity, key hashes, revocation and rotation;
- `phases`: validation/official configuration and limits;
- `episodes`: ordered immutable observation and settlement inputs;
- `runs`: one team attempt or official portfolio timeline;
- `run_steps`: current step, observation hash, state before/after settlement;
- `submissions`: raw decision, sanitized decision, violations, server receipt time;
- `portfolio_states`: cash, shares, NAV, drawdown and queued target;
- `leaderboard_snapshots`: reproducible derived outputs;
- `audit_events`: administrative and participant security events.

SQLite is appropriate for the interface prototype and a single-process rehearsal. Production
should use PostgreSQL before multiple API workers or scheduled settlement jobs are introduced.

## 7. Hosting decision

The competition server is deployed on organizer-controlled infrastructure, not "to Codabench".
A rented cloud VM is a reasonable staging starting point. Production selection should consider
region/latency, managed PostgreSQL, object storage, TLS/load balancer, backups, monitoring, and
failure recovery. A single VM plus SQLite is not the final high-availability design.

Codabench may separately receive final code archives and later display mirrored leaderboard
values. Whether its organizer API is convenient for automated score updates is an integration
experiment, not a dependency for the core server.

## 8. Explicitly deferred decisions

- exact validation episode dates/length and whether all teams see the same episode;
- validation attempt reset semantics after crashes;
- official daily deadline (the PDF currently implies U.S. market close);
- whether an accepted official decision can be replaced before the deadline (the PDF says one
  official decision, so the draft defaults to first accepted decision wins);
- missing-decision violation/scoring details;
- final metric aggregation and audit/disqualification policy;
- production market/fundamental source and correction policy;
- Codabench identity reconciliation, final archive format, and leaderboard mirroring.

