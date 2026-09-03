# PostgreSQL participant receiver

This service is the participant-facing intake boundary. It authenticates teams,
serves already-published team observations, enforces the explicit trading-day
submission window and one accepted decision per team/day, and stores every
authenticated submission attempt for audit.

It deliberately does **not** sanitize weights, create executions, settle
portfolios, or calculate the leaderboard. The Competition component consumes
`decision_submissions` rows in `RECEIVED` state and owns those later steps.

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
  --submission-deadline-at 2026-09-02T13:29:59Z
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
