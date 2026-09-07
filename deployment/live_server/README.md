# PostgreSQL participant receiver

This service is the participant-facing intake boundary. It authenticates teams,
serves already-published team observations, enforces the explicit trading-day
submission window and one accepted decision per team/day, and stores every
authenticated submission attempt for audit.

It deliberately does **not** sanitize weights, create executions, settle
portfolios, or calculate the leaderboard. The Competition component consumes
`decision_submissions` rows in `RECEIVED` state and owns those later steps.

## Organizer daily automation

The participant API does not create calendar rows as part of a participant
request. `POST /api/v1/admin/trading-days` is an organizer control-plane and
recovery endpoint that happens to be hosted by the same FastAPI process.

For an end-of-day live run, configure `COMPETITION_DATABASE_URL` and invoke:

```bash
python -m deployment.live_server.daily_live
```

Without `--date`, the command selects the latest XNYS session whose official
close has passed. `--date YYYY-MM-DD` is available for recovery and backfills.
The official XNYS calendar must already have been provisioned by an organizer.
The command validates the requested signal day, its next execution session, and
the decision cutoff (30 minutes before the next exchange open). It never creates
or extends official calendar rows. It then downloads one daily Yahoo Finance bar
for every active instrument and commits the day only when the complete universe
is present. Re-running it is safe.

`DailyCompetitionService` owns the platform workflow: it freezes received
weights with Competition's reject-not-repair validator, creates explicit hold
fallbacks, and publishes the next observations. Its thin `CompetitionAdapter`
converts ticker-facing platform data to the core's Decimal/instrument-id input,
then delegates accounting and trading persistence to Competition's
`advance_day()` and `TradingRepository`. Fundamentals remain an empty object in
the observation until a separate point-in-time feed is integrated.

The complete team batch and all observations commit atomically. Re-running a
completed day returns `state=already_completed` without charging fees twice. A
production scheduler (CronJob, systemd timer, or equivalent) should invoke this
command after the provider's end-of-day data is complete.

## Database migrations

Deployment owns additive database migrations under `deployment/live_server/migrations`.
Do not edit an already-applied migration: the runner records and verifies its SHA-256.
The Compose `schema-init` service creates the teammate-owned base schema only when the database
is empty, then applies all pending Deployment migrations. Existing databases never replay the
base schema over migrated views. The organizer bootstrap command follows the same sequence.

For an existing database, stop the daily scheduler at a completed-day boundary, take a
PostgreSQL backup, and run:

```bash
python -m deployment.live_server.schema_init
python -m deployment.bootstrap.verify_setup
```

Migration `0001_prior_close_snapshot` adds
`portfolio_snapshots.prior_close_snapshot_id`, backfills execution snapshots from the
authoritative `execution -> submission -> observation -> CLOSE snapshot` relationship,
and links legacy bootstrap snapshots to their immediately preceding account state. It
fails rather than guessing if any non-initial snapshot cannot be linked or violates the
team/type/time ordering invariants.

`INITIAL` snapshots have a null predecessor. Every `POST_OPEN` and `CLOSE` snapshot has
the same predecessor selected by its execution's source observation. A database trigger
fills this Deployment-owned column for new rows, so the Competition persistence package
does not need to know about the added column.

Application rollout order:

1. Pause the daily scheduler and back up PostgreSQL.
2. Deploy and run `deployment.live_server.schema_init`.
3. Require `verify_setup` to report `deployment_migrations_current=true` and
   `snapshot_lineage_valid=true`.
4. Deploy/restart the live server and resume the scheduler.

Rollback is restore-from-backup. Do not drop the column after new snapshots have used it;
that would discard production lineage rather than perform a safe application rollback.

## Run

```bash
export COMPETITION_DATABASE_URL='postgresql://competition:competition@127.0.0.1:5432/competition'
export COMPETITION_ADMIN_TOKEN='<high-entropy organizer token>'
python -m deployment.live_server.schema_init
python -m deployment.live_server.app
```

The Docker Compose service starts the same application after PostgreSQL is
healthy and the schema initializer has completed.

## Organizer setup

```bash
python -m deployment.live_server.manage register-team team_001 --display-name 'Team 001'

python -m deployment.live_server.manage create-trading-day 2026-09-01 \
  --market-open-at 2026-09-01T13:30:00Z \
  --market-close-at 2026-09-01T20:00:00Z \
  --submission-open-at 2026-09-01T20:00:00Z \
  --submission-deadline-at 2026-09-02T13:00:00Z
```

The plaintext team key is printed once. Observations and their referenced
portfolio snapshots are populated by the Data/Competition workflow.

## Downstream handoff

Competition may select raw work with:

```sql
SELECT id, team_id, observation_id, signal_day_id, execution_day_id,
       raw_payload_json, payload_hash, received_at
FROM decision_submissions
WHERE status = 'RECEIVED'
ORDER BY received_at, id;
```

Unknown assets, negative weights, caps, gross exposure, fallback behavior, and
all execution state are intentionally unresolved at this boundary.
