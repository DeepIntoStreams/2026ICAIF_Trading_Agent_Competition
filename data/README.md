# Competition data and database contract

This directory defines the shared data contract for the live competition. The
contract and bootstrap files are committed to Git; local database files and
actual datasets are not.

## Contents

- `database/README.md` — authoritative database design and live processing flow.
- `database/LIVE_WORKFLOW_EXAMPLE.zh-CN.md` — complete Chinese implementation example.
- `database/LIVE_WORKFLOW_EXAMPLE.en.md` — matching English implementation example.
- `database/schema.sql` — executable SQLite schema used by every developer.
- `database/init_db.py` — creates and validates a local database from the schema.
- `database/competition.sqlite3` — generated local database; ignored by Git.
- `datasets/` — local market/fundamental inputs; ignored by Git.
- `imports/` — temporary provider downloads; ignored by Git.

All data actually used by the competition is imported into the database before
processing. Files under `datasets/` are inputs only and are never authoritative
competition state.

Create a local database:

```bash
python data/database/init_db.py
```

Create it at another path:

```bash
python data/database/init_db.py --db /absolute/path/to/competition.sqlite3
```

The command is safe to run again. It applies idempotent `CREATE ... IF NOT
EXISTS` statements and performs SQLite integrity and foreign-key checks.
