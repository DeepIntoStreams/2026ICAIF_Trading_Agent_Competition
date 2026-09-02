# Competition data and database contract

This directory defines the shared data contract for the live competition. The
contract and bootstrap files are committed to Git; local database files and
actual datasets are not.

## Contents

- `database/README.md` — authoritative database design and live processing flow.
- `database/LIVE_WORKFLOW_EXAMPLE.zh-CN.md` — complete Chinese implementation example.
- `database/LIVE_WORKFLOW_EXAMPLE.en.md` — matching English implementation example.
- `database/schema.sql` — executable PostgreSQL schema used by every developer.
- `database/init_db.py` — applies and validates the schema on PostgreSQL 16.
- `datasets/` — local market/fundamental inputs; ignored by Git.
- `imports/` — temporary provider downloads; ignored by Git.

All data actually used by the competition is imported into the database before
processing. Files under `datasets/` are inputs only and are never authoritative
competition state.

Start PostgreSQL and apply the schema:

```bash
docker compose -f deployment/docker-compose.yml up -d postgres
python data/database/init_db.py
```

Create it at another path:

```bash
python data/database/init_db.py \
  --database-url postgresql://competition:competition@127.0.0.1:5432/competition
```

The command is safe to run again because the schema uses idempotent creation
statements. Set `COMPETITION_DATABASE_URL` instead of putting production secrets
on a command line.
