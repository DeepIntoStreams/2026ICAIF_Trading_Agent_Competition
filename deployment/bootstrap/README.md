# Production bootstrap

These commands provision and validate the database state required by the live
competition system. Run them from the repository root. Keep the official
instrument universe, schedule, constraints, database URI, and provider
credentials outside Git and pass the configuration path explicitly.

## Commands

- `init_database.py` applies the final PostgreSQL schema, synchronizes the
  configured instrument universe, provisions exchange sessions, and can import
  verified market bars.
- `verify_setup.py` checks the instrument universe, calendar, imported-day
  completeness, and portfolio snapshot lineage.
- `publish_initial_observations.py` publishes the first observation for every
  active team from its funded `INITIAL` snapshot. Later observations are
  published by daily settlement.

Generated run records are operational artifacts and are ignored by Git. The
commands redact database passwords from their summaries, but their output
should still be handled as internal operational data.

## Initialize and verify

```bash
export COMPETITION_DATABASE_URL='<PostgreSQL connection URI from the secret manager>'
export COMPETITION_CONFIG_PATH='<path to the frozen competition configuration>'

python -m deployment.bootstrap.init_database \
  --config "$COMPETITION_CONFIG_PATH" \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD

python -m deployment.bootstrap.verify_setup \
  --config "$COMPETITION_CONFIG_PATH"
```

Add `--fetch-market-data` only after the required provider credentials are
available to the process. Initialization is repeat-safe and does not clear an
existing database; incompatible existing state causes the command to fail.

## Publish the first observations

Before publication, register the official teams, issue credentials privately,
fund exactly one `INITIAL` portfolio snapshot per team, and import the complete
first signal-day market data. Then run:

```bash
python -m deployment.bootstrap.publish_initial_observations \
  --config "$COMPETITION_CONFIG_PATH" \
  --date YYYY-MM-DD
```

This command is atomic and repeat-safe. It does not execute a synthetic first
trade: each first observation references the team's funded `INITIAL` snapshot.

## Daily operation

Data collection and settlement can be retried independently:

```bash
python -m deployment.live_server.daily_live --stage collect --date YYYY-MM-DD
python -m deployment.live_server.daily_live --stage settle --date YYYY-MM-DD
```

Use `--stage all` for the compatible collect-then-settle path. Production must
also provide TLS termination, a secret manager, a least-privilege database
role, backups with restore drills, clock synchronization, monitoring, and a
single authoritative scheduler.
