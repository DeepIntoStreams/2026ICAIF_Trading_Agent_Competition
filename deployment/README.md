# ICAIF 2026 competition deployment

PostgreSQL 16 is the single database baseline for local integration, staging,
and production. SQLite is no longer part of the target architecture.

## Current implementation boundary

The participant receiver runs exclusively on PostgreSQL. It authenticates
teams, serves published observations, applies the explicit calendar cutoff,
enforces one accepted decision per team/day, and persists raw decisions plus a
complete attempt/audit trail.

The receiver does not sanitize weights or write executions. Those operations
belong to Competition, which consumes `decision_submissions` in `RECEIVED`
state.

The target flow is documented in:

- `../data/database/README.md`
- `../data/database/LIVE_WORKFLOW_EXAMPLE.en.md`
- `../data/database/LIVE_WORKFLOW_EXAMPLE.zh-CN.md`

## Start the local PostgreSQL database

```bash
docker compose -f deployment/docker-compose.yml up -d postgres
docker compose -f deployment/docker-compose.yml run --rm schema-init
```

Default local connection:

```text
postgresql://competition:competition@127.0.0.1:5432/competition
```

Override `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`POSTGRES_PORT` in an uncommitted `.env` when needed. Production must use managed
secrets and a non-default password.

Without Docker, point the initializer at an existing PostgreSQL 16 instance:

```bash
export COMPETITION_DATABASE_URL='postgresql://user:password@host:5432/database'
python data/database/init_db.py
```

## Verify the schema

```bash
psql "$COMPETITION_DATABASE_URL" -c '\dt'
psql "$COMPETITION_DATABASE_URL" -c '\dv'
```

Expected result: 16 application tables and 3 convenience views.

## Run the receiver

```bash
export COMPETITION_ADMIN_TOKEN='<high-entropy organizer token>'
docker compose -f deployment/docker-compose.yml up --build live-server
```

See `live_server/README.md` for team/calendar initialization and the
Competition database handoff.
