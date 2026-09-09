# PostgreSQL participant receiver

This service is the participant-facing intake boundary. It authenticates teams,
serves already-published team observations, enforces the explicit trading-day
submission window and one accepted decision per team/day, and stores every
authenticated JSON submission attempt that passes the transport guards for audit.

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
or extends official calendar rows. It checks SEC EDGAR for newly accepted 10-Q/
10-K filings, then downloads each daily bar from Yahoo Finance, Massive, and
Twelve Data. A bar is committed only when at least two of the three independent
sources agree within the price and volume tolerances and the complete universe
passes structural validation.
Provider candidates and the selected quorum are retained for audit. Re-running
the daily job is safe; a day with no new filing is a successful no-op.

### Confirmed no-trading handoff

A missing provider response is never treated as proof of a halt. An organizer
market-status collector must first write an attributable full-session event to
`instrument_market_events`, including `confirmation_source`,
`confirmation_reference`, and `confirmed_at`. The market importer then carries
the most recent actually tradable close for valuation, stores volume zero, and
writes the official bar with:

```text
is_tradable = false
verification_status = CONFIRMED_NO_TRADING
market_event_id = <the confirmed event>
```

If any provider reports real traded data for that date, the event conflicts
with the bars and the import fails for operator review. Without a confirmed
event, missing data or a failure to obtain a two-source quorum also fails.

When Massive and Twelve Data agree but Yahoo is absent or is the outlier, their
consensus is stored as the official bar. Yahoo's candidate remains in
`market_bar_candidates` when present, and `verification_status` distinguishes
`PRIMARY_MISSING` from `PRIMARY_DISAGREEMENT`.

The settlement workflow honors `market_bars.is_tradable` without changing the
Competition core. After restoring the prior portfolio, the Deployment adapter
replaces a non-tradable instrument's requested target with the exact weight
that preserves its current share quantity, then calls the existing core. The
original participant target remains unchanged in the submission audit tables.
The published observation exposes the completed session's `is_tradable` and
`verification_status` values; these describe the finished signal day and do
not claim that the instrument will also be halted on the next execution day.

`DailyCompetitionService` owns the platform workflow: it freezes received
weights with Competition's reject-not-repair validator, creates explicit hold
fallbacks, and publishes the next observations. Its thin `CompetitionAdapter`
converts ticker-facing platform data to the core's Decimal/instrument-id input,
then delegates accounting and trading persistence to Competition's
`advance_day()` and `TradingRepository`. The observation selects only SEC filing
versions whose EDGAR acceptance time is no later than the panel-generation time
(and never later than the decision deadline). Quarter-end dates are never used
as availability timestamps.

The complete team batch and all observations commit atomically. Re-running a
completed day returns `state=already_completed` without charging fees twice. A
production scheduler (CronJob, systemd timer, or equivalent) should invoke this
command after the provider's end-of-day data is complete.

Data collection and settlement are independently operable. This keeps a
settlement retry away from SEC and market-data providers:

```bash
python -m deployment.live_server.daily_live --stage collect --date YYYY-MM-DD
python -m deployment.live_server.daily_live --stage settle --date YYYY-MM-DD
```

`--stage all` remains the backward-compatible collect-then-settle command.

## Database schema

`data/database/schema.sql` is the final database definition. The Compose `schema-init`
service and the organizer bootstrap command apply it directly; Deployment does not keep
a separate database schema.

`INITIAL` snapshots have a null predecessor. Every `POST_OPEN` and `CLOSE` snapshot has
the same predecessor selected by its execution's source observation. A database trigger
fills and validates this column for new rows.

## Run

```bash
export COMPETITION_DATABASE_URL='<PostgreSQL connection URI from the secret manager>'
export COMPETITION_ADMIN_TOKEN='<high-entropy organizer token>'
export COMPETITION_RUN_ID='<frozen competition run identifier>'
export SEC_EDGAR_USER_AGENT='<organizer name and monitored contact address>'
export MASSIVE_API_KEY='<market-data-api-key>'
export TWELVE_DATA_API_KEY='<market-data-api-key>'
python -m data.database.init_db
python -m deployment.live_server.app
```

The Docker Compose service starts the same application after PostgreSQL is
healthy and the schema initializer has completed.

## Organizer setup

```bash
python -m deployment.live_server.manage register-team TEAM_CODE --display-name 'Team name'

python -m deployment.live_server.manage create-trading-day YYYY-MM-DD \
  --market-open-at '<UTC timestamp>' \
  --market-close-at '<UTC timestamp>' \
  --submission-open-at '<UTC timestamp>' \
  --submission-deadline-at '<UTC timestamp>'
```

The plaintext team key is printed once. Observations and their referenced
portfolio snapshots are populated by the Data/Competition workflow.

Participant credentials are stored in `team_api_credentials`. Organizer API endpoints can rotate
a key immediately or keep old keys valid for a grace period of up to one hour, and can revoke a
specific credential. Only hashes are stored; a newly generated plaintext key is returned once.

## Deadline burst protection

The receiver timestamps a decision after its complete request body arrives, before it waits for a
database worker. This prevents a slow body from crossing the deadline while keeping a legitimate
pre-deadline burst fair when PostgreSQL writes finish shortly afterward. Streaming size limits,
body timeouts, per-credential/IP token buckets, a global in-flight cap, and a dedicated bounded DB
thread pool limit memory and connection pressure. Supported variables and their development
defaults are listed in `deployment/docker-compose.yml`; production values belong in the platform's
secret and configuration stores, not in Git.

The application limiter is per process. A multi-worker or multi-replica deployment must enforce a
shared outer rate limit at its trusted reverse proxy.

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
