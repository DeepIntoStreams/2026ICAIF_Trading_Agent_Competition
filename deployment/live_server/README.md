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

## Run

```bash
export COMPETITION_DATABASE_URL='postgresql://competition:competition@127.0.0.1:5432/competition'
export COMPETITION_ADMIN_TOKEN='<high-entropy organizer token>'
python data/database/init_db.py
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
