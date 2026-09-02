# ICAIF 2026 competition deployment

PostgreSQL 16 is the single database baseline for local integration, staging,
and production. SQLite is no longer part of the target architecture.

## Current implementation boundary

The PostgreSQL schema and reproducible local database bootstrap are ready. The
existing `deployment/live_server/store.py` is the previous SQLite API prototype
and is deliberately not started by the new Compose file. It must be replaced by
repositories and services implementing the PostgreSQL contract before the HTTP
server is considered runnable again. This prevents accidental operation against
the obsolete four-table state model.

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

Expected result: 15 application tables and 3 convenience views.

## Next implementation step

Replace the legacy `LiveStore` with PostgreSQL repositories, then implement the
two-transaction submission/weight handoff and end-of-day execution workflow.
The HTTP API must not be re-enabled in Compose until it reads and writes the new
schema exclusively.
