# Live competition database design

## 1. Scope and invariants

This schema is intentionally designed for one live competition. It does not
introduce competition, phase, episode, run, dataset-version, or universe-version
abstractions.

The database is the authoritative record of:

- imported market and fundamental data used by the competition;
- the exact observation served to each team;
- every team's one allowed submission for a signal day;
- raw and sanitized target weights;
- validation results;
- simulated executions and per-instrument transactions;
- every cash movement;
- portfolio cash, positions, NAV, and weights at defined points in time;
- daily returns and cumulative performance;
- lifecycle and security audit events.

The core identity of a live workflow is `team_id + trading_day_id`. There is one
official portfolio trajectory per team.

Implementation ownership is split at `decision_submissions.status = 'RECEIVED'`:
Deployment authenticates, enforces the calendar window and daily limit, records
every attempt, and persists the untouched participant document. Competition owns
weight sanitation, fallback decisions, executions, valuation, and scoring.

Important invariants:

1. A team can submit at most once for a signal day. Repeating the same
   idempotency key returns the stored receipt; a different second submission is
   rejected by the unique database constraint.
2. A decision based on day T data is scheduled for execution at day T+1 open.
3. Missing tickers in a submitted target mean target weight zero.
4. Receiver acceptance leaves a submission at `RECEIVED` without normalized
   weights. Competition later stores one row per active instrument.
5. Submitted weights never directly change cash or positions. Only a completed
   execution creates transactions and changes the portfolio.
6. An execution uses day T+1 open prices. Day T+1 performance uses day T+1 close
   prices, even when both calculations are processed together after the close.
7. Historical facts are append-only. Only workflow state columns and current
   status fields are updated.

## 2. Why end-of-day batch execution is valid

The server does not need the new portfolio during the trading session. It may
therefore process day T+1 after the close in one deterministic batch:

1. import the complete T+1 daily bars;
2. execute the accepted T decision using the stored T+1 open prices;
3. write transactions and the post-open portfolio snapshot;
4. value that portfolio using T+1 close prices;
5. calculate T+1 performance;
6. build and publish the T+1 observation for the next submission window.

This produces the same simulated holdings and P&L as processing at the physical
open, provided that:

- the T submission deadline is before the T+1 open;
- the T+1 open/close data is not exposed before the deadline;
- `effective_at` records when the simulated execution economically occurred;
- `processed_at` records when the server actually ran the batch;
- the opening execution is completed before the T+1 close valuation is computed.

This approach imports the daily market data once and avoids a second intraday
provider call.

## 3. Trading-day rows and state changes

`trading_days` contains exactly one row per market date. A new market date inserts
one new row. Processing that date updates the row's workflow columns:

```text
CREATED -> DATA_IMPORTED -> EXECUTED -> VALUED -> OBSERVATION_PUBLISHED
```

The row is not duplicated for each transition. Every transition is additionally
appended to `audit_logs`, which preserves who changed it, when, and why. This
gives simple current-state queries and a complete transition history.

There is no ambiguous observation status. An observation is a stored immutable
fact; `generated_at` and nullable `published_at` state whether it has merely been
generated or has actually been made available to a team.

## 4. Table relationships

```text
teams
  +-- submission_attempts
  +-- observations
  +-- decision_submissions
  |     +-- submission_weights
  |     +-- executions
  |           +-- transactions
  |           +-- cash_ledger
  +-- portfolio_snapshots
  |     +-- position_snapshots
  +-- daily_performance

trading_days
  +-- market_bars
  +-- fundamental_records
  +-- observations
  +-- decision_submissions (signal day and execution day)
  +-- executions
  +-- portfolio_snapshots
  +-- daily_performance
```

Foreign keys avoid repeating team/date values on every weight row. Convenience
views at the end of `schema.sql` expose denormalized rows for ordinary reads, so
application code does not need a sequence of per-row queries.

## 5. Table definitions and meaning

### `teams`

One row per approved team. `api_key_hash` stores only a password hash/digest; the
plaintext credential must never be stored or committed.

### `instruments`

The fixed tradable stock list. `ticker` is unique. Market bars, weights,
transactions, and positions reference one stable instrument ID.

### `trading_days`

One row per exchange trading date. It stores the submission window and current
workflow state. Times are ISO-8601 UTC strings. The state columns are mutable
coordination data; changes are also written to `audit_logs`.

### `market_bars`

One complete OHLCV record per instrument and trading day. The values used for
execution and valuation are therefore reproducible from the database alone.

### `fundamental_records`

Point-in-time fundamental input records. `available_at` controls whether a record
may appear in an observation. The original provider document is retained in
`payload_json`.

No organizer-provided news table is present because news is not sent in the
competition observation.

### `observations`

The exact immutable JSON payload served to a particular team for a particular
signal day. It contains the complete day-T market information, permitted
fundamentals, constraints, and that team's close portfolio state. It contains no
news. `payload_hash` detects accidental mutation and supports audit reproduction.

### `decision_submissions`

The complete HTTP decision envelope and server receipt. The unique
`(team_id, signal_day_id)` constraint enforces one submission per team per signal
day. `execution_day_id` explicitly links the decision to the next trading day;
the engine never guesses the execution date from calendar arithmetic.

`status` describes receipt/validation/execution progress. A structurally valid
submission may still fail semantic validation. The configured reject-not-repair
validator then rejects the complete vector; the raw document remains unchanged.

`validator_version` and `validation_policy_json` freeze the exact validation
implementation and constraint values used for this submission. Validation rules
may evolve without making an older stored result ambiguous.

These validation fields are null while status is `RECEIVED`. The Deployment
receiver never fills them; Competition freezes them when processing begins.

### `submission_attempts`

One append-only row is written for every authenticated decision request. It
records accepted requests, invalid envelopes, late requests, daily-limit
rejections, idempotent replays, and idempotency conflicts. Only an `ACCEPTED`
attempt creates a canonical `decision_submissions` row.

An organizer-created `FALLBACK` submission is not an HTTP attempt, so its
`intake_attempt_id` is null. Its system origin remains explicit in
`decision_submissions.source` and the audit log.

### `submission_weights`

The execution-facing normalized target vector. There is one row per instrument
for each submission:

- `was_provided` says whether the ticker appeared in the participant payload;
- `raw_weight` is nullable for an omitted or non-numeric ticker (the exact raw
  value always remains available in `decision_submissions.raw_payload_json`);
- `sanitized_weight` is the frozen numeric target consumed by execution for an
  accepted vector (the column name is retained for schema compatibility);
- `validation_codes_json` records zero or more validation codes.

The validator inserts the complete vector in one transaction. Execution loads it
with one set query:

```sql
SELECT instrument_id, sanitized_weight
FROM submission_weights
WHERE submission_id = ?
ORDER BY instrument_id;
```

This is not an N+1 query. To inspect whether an asset was actually bought or sold,
query `transactions`, not target weights. A target is an instruction; a
transaction is the resulting trade.

The `v_submission_weight_details` view already joins team, signal date, execution
date, ticker, raw weight, and sanitized weight for analysis.

### Exact receiver-to-Competition handoff

The receiver stores the accepted attempt, raw canonical submission, and audit
event in one transaction. It does not invoke the validator.

**Receiver transaction:**

```sql
INSERT INTO decision_submissions (..., status, expected_weight_count,
                                  stored_weight_count, ...)
VALUES (..., 'RECEIVED', :observation_asset_count, 0, ...)
RETURNING id;
```

The returned database-generated `id` is returned to the participant and is the
only ID Competition may use for normalized weights.

**Competition-owned weight-processing transaction:**

1. load and lock the `RECEIVED` submission by ID;
2. parse `raw_payload_json.target_weights`;
3. load the complete active instrument list;
4. call the versioned pure weight validator;
5. bulk-insert one `submission_weights` row per active instrument using the same
   `submission_id`;
6. verify the inserted count equals `expected_weight_count`;
7. update `stored_weight_count`, `sanitized_gross_weight`, and
   `weights_processed_at`;
8. update an accepted submission to `QUEUED`; the after-close trading repository
   creates and completes its execution atomically with the trading trajectory;
9. commit everything together.

If processing fails, the raw submission remains `RECEIVED` and can be resumed by
ID. If validation rejects it, the service stores a complete zero diagnostic
vector and changes the submission to `REJECTED`. The after-close hold path still
persists a zero-trade execution, CLOSE valuation, performance, and observation.

The execution engine never parses raw participant JSON and never sanitizes
weights. Its database adapter accepts only a `QUEUED` submission whose stored
count equals its expected count, then builds this explicit input:

```text
ExecutionInput
  execution_id
  submission_id
  team_id
  signal_day_id
  execution_day_id
  prior_portfolio_snapshot_id
  targets[]: {instrument_id, sanitized_weight}
  open_prices: {instrument_id, adjusted_open}
  fee_rate
  engine_version
```

This is the stable boundary between submission storage and execution. Changing
sanitization rules changes the validator output and its stored version/policy; it
does not change the engine's input shape.

### `executions`

One execution batch per accepted submission. It is created as `PENDING` after
validation and points to both the submission and its execution trading day.
`engine_version` identifies the exact execution implementation used.

Processing uses this query pattern:

```sql
SELECT e.id, e.team_id, e.submission_id, e.trading_day_id
FROM executions e
WHERE e.trading_day_id = ? AND e.status = 'PENDING';
```

For each returned execution, the engine loads all target weights, the team's
latest portfolio snapshot, and that day's open prices. On success it writes the
transactions, cash entries, and post-open portfolio atomically, then marks the
execution `COMPLETED`.

### `transactions`

One row per simulated instrument trade. One execution normally produces many
transactions. Since this competition simulates one deterministic fill per asset,
separate order and fill tables are unnecessary. `status` records whether that
asset trade completed, was skipped, or failed.

`cash_change` contains the net cash effect of the trade, including its fee. The
same movement is referenced by `cash_ledger` because a trade record explains
market activity while the ledger provides a complete, ordered cash balance that
also includes initial capital and non-trade adjustments.

### `cash_ledger`

Append-only cash movements. Trade entries reference a transaction. Initial
capital and administrative adjustments have no transaction. `balance_after`
allows fast verification and detects broken cash continuity.

### `portfolio_snapshots` and `position_snapshots`

Together these two relational tables represent one portfolio:

- `portfolio_snapshots` is the portfolio header: cash, total position value, NAV,
  exposure, and snapshot time;
- `position_snapshots` contains the portfolio's per-instrument positions.

They are split only because one portfolio contains many positions. The supported
snapshot types are:

- `INITIAL`: initial cash portfolio;
- `POST_OPEN`: after the simulated open execution;
- `CLOSE`: after close-price valuation.

### `daily_performance`

One summary row per team and trading day. It is derived after the close snapshot
and stores daily return, cumulative return, transaction cost, turnover, and
drawdown. It is a convenient immutable daily summary; transactions and snapshots
remain the underlying evidence.

### `audit_logs`

Append-only operational and security events, including workflow transitions,
authentication failures, rejected duplicates, execution failures, and manual
actions. It is not used to calculate portfolio state.

## 6. Complete live interaction

### A. End-of-day batch for T

Within a database transaction or an idempotent job:

1. insert the complete T market bars and fundamentals;
2. set T `market_status = DATA_IMPORTED`;
3. find `QUEUED` submissions whose `execution_day_id = T`, plus rejected or
   missing submissions that must follow the hold path;
4. execute or hold each portfolio using T open prices;
5. insert transactions and cash-ledger entries;
6. insert each team's `POST_OPEN` portfolio and positions;
7. set each execution to `COMPLETED`;
8. value the resulting portfolios at T close;
9. insert `CLOSE` portfolio and position snapshots;
10. insert T daily performance;
11. generate and store each team's T observation with no news;
12. publish the observations and open the T submission window.

The T observation therefore describes the T close state and creates targets for
T+1 open.

### B. Participant read

The authenticated team reads its T observation. The server records the delivery
time in `observations.first_served_at` and may append an audit event.

### C. Participant submission

The participant submission uses the two transactions defined in the exact
handoff above:

1. authenticate the team and verify T's deadline;
2. receipt transaction: insert exactly one `decision_submissions` row with the
   full raw JSON, commit it, and retain the returned ID;
3. processing transaction: validate every active instrument, populate raw and
   frozen target values, verify completeness, and queue an accepted vector for
   T+1;
4. return the final stored receipt.

The unique constraints are the final concurrency guard. A repeated idempotency
key returns the first receipt. A second distinct submission for the same team and
signal day is rejected and logged; it does not replace the first.

### D. Missing submission

At the deadline, a team without a T submission gets a server-created fallback
decision according to competition policy (for example, retain the prior target or
current holdings). The fallback is stored as a decision, normalized weights, and
an execution like any participant decision, with `source = FALLBACK` and an audit
event. This keeps execution free of hidden special cases.

## 7. Supported operations

The schema directly supports:

- import complete daily market/fundamental data;
- publish one immutable observation per team/day;
- accept exactly one idempotent decision per team/day;
- retain the complete raw decision document;
- store complete raw and sanitized target vectors;
- record multiple validation codes per instrument as JSON;
- schedule a decision explicitly for the next trading day;
- execute many instrument transactions in one atomic execution batch;
- record all fees and cash changes;
- reconstruct cash and positions at every official snapshot;
- calculate close-to-close daily and cumulative performance;
- determine whether an instrument was targeted, bought, sold, or held;
- reproduce any execution from submission, prices, and prior portfolio state;
- audit lifecycle changes and rejected duplicate requests;
- resume safely by selecting only `PENDING` work.

## 8. Common queries

Load the complete target vector for execution:

```sql
SELECT instrument_id, sanitized_weight
FROM submission_weights
WHERE submission_id = ?;
```

Find all trades in AAPL for one team:

```sql
SELECT *
FROM v_transaction_history
WHERE team_code = ? AND ticker = 'AAPL'
ORDER BY trading_date, transaction_id;
```

Determine whether a submitted target produced a trade:

```sql
SELECT sw.submission_id, i.ticker, sw.sanitized_weight,
       tx.side, tx.quantity, tx.price, tx.status
FROM submission_weights sw
JOIN instruments i ON i.id = sw.instrument_id
LEFT JOIN executions e ON e.submission_id = sw.submission_id
LEFT JOIN transactions tx
  ON tx.execution_id = e.id AND tx.instrument_id = sw.instrument_id
WHERE sw.submission_id = ?;
```

Load the latest close portfolio:

```sql
SELECT ps.*
FROM portfolio_snapshots ps
WHERE ps.team_id = ? AND ps.snapshot_type = 'CLOSE'
ORDER BY ps.effective_at DESC
LIMIT 1;
```

## 9. Transaction boundaries

The following operations must each be atomic:

- raw submission receipt;
- normalized weights + submission transition + pending execution creation;
- execution + transactions + cash ledger + post-open portfolio;
- close valuation + positions + daily performance;

An execution has `effective_at` and `processed_at`. Retrying a batch checks the
unique `(submission_id)` execution constraint and its status before writing, so a
completed execution cannot be charged twice.

PostgreSQL is the only supported database baseline for local integration,
staging, and production. Developers run the same major PostgreSQL version in
Docker, avoiding dialect and concurrency differences between development and
the live service.
