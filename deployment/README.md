# ICAIF 2026 competition server

This directory contains the organizer-operated service for both Validation and the Official
Competition. Participants always run agents locally. The server authenticates teams, serves
official observations and private portfolio state, accepts target weights, performs deterministic
next-open settlement, and stores local leaderboard results.

Codabench is not part of the runtime path. A future isolated integration may use it for final
code-archive intake or mirror organizer-computed leaderboard values.

## Current layout

```text
deployment/
  README.md
  ARCHITECTURE.md       system boundary, shared state machine, hosting and security design
  API.md                runnable v0.1 HTTP interface and examples
  Validation_and_Official_Competition_Interaction_Logic.pdf
  docker-compose.yml
  starter_kit/         participant HTTP client and replaceable mock agent
  live_server/          legacy package name; currently the unified competition service
    app.py              participant and organizer HTTP endpoints
    store.py            SQLite identity, sessions, decisions, settlement and leaderboard
    manage.py           local organizer CLI
    Dockerfile
  tests/
    test_live_store.py
```

The `live_server` package name is retained temporarily to avoid a noisy rename while the API is
still evolving. Its responsibility is no longer limited to the live phase.

## Phase model

Validation and Official Competition use the same authentication, observation, decision, risk,
next-open execution, transaction-cost, and metric logic.

- Validation rapidly advances through a configured historical episode and permits multiple
  attempts.
- Official Competition advances through the same state machine once per real trading day and
  accepts one official decision per team/session.

The historical Validation run/episode controller is the next implementation increment. The
current runnable subset covers authentication, published-session observations, decisions,
next-open settlement, state, and local leaderboard computation.

## Local start

With Docker:

```bash
COMPETITION_ADMIN_TOKEN='replace-with-a-long-random-secret' docker compose \
  -f deployment/docker-compose.yml up --build
```

For direct Python development, install project dependencies and run:

```bash
COMPETITION_ADMIN_TOKEN='replace-with-a-long-random-secret' PYTHONPATH=src \
  python -m deployment.live_server.app --db /tmp/competition.sqlite3
```

Register a team and securely save the API key printed once:

```bash
PYTHONPATH=src python -m deployment.live_server.manage \
  --db /tmp/competition.sqlite3 register-team team_001
```

See `API.md` for participant requests and `ARCHITECTURE.md` for production gates.

When running locally, interactive FastAPI documentation is available at
`http://127.0.0.1:8080/docs`; the OpenAPI contract is at `/openapi.json`.
