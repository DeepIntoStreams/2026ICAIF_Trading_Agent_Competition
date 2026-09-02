# Complete Live Competition Implementation Example (English)

This document uses one concrete example to specify how the future code should be organized, how it should interact with the database, and how a submitted target vector becomes transactions, cash, positions, and performance. It is an implementation contract; the described business services have not yet been implemented.

Companion files:

- `schema.sql`: the executable source of truth for fields and constraints;
- `README.md`: the overall database design;
- this document: the end-to-end example and proposed code interfaces.

## 1. Scenario

Team `team_alpha` reads its observation after the 2026-09-01 close and may submit target weights exactly once. Those targets execute at the 2026-09-02 open. After the complete 2026-09-02 daily bar arrives, the server processes the opening execution retrospectively, values the resulting portfolio at the close, calculates performance, and publishes the next observation in one end-of-day batch.

```text
Signal day T                 2026-09-01
T market close              2026-09-01T20:00:00Z
Submission window           after T close and before T+1 open
Submission deadline         2026-09-02T13:29:59Z
Execution day T+1           2026-09-02
Economic execution time     2026-09-02T13:30:00Z
Server processing time      after the complete 2026-09-02 bar arrives
```

`effective_at` is when an event economically occurred in the simulation. `processed_at` is when the server actually calculated and stored it.

## 2. Proposed code boundaries

```text
deployment/live_server/
  app.py                    HTTP routing, authentication, request checks
  repositories.py           SQL and database object mapping only
  submission_service.py     one-shot receipt, normalization, validation, queueing
  daily_service.py          idempotent end-of-day orchestration and recovery
  observation_service.py    build and persist each team's observation
  execution_service.py      load targets and simulate at the open
  valuation_service.py      create close snapshots and performance
  models.py                 API request and response models

src/competition_core/
  weight_validator.py       pure: raw weights -> sanitized weights + codes
  execution_engine.py       pure: portfolio + targets + opens -> trades + portfolio
  valuation_engine.py       pure: portfolio + closes -> NAV and actual weights
  metrics_engine.py         pure: daily performance history -> competition metrics
  observation_builder.py    pure: complete T data + T close portfolio -> payload
```

`competition_core` must not connect to the database, start HTTP, or retain global state. It performs deterministic calculations. `deployment` owns transactions, persistence, authentication, and scheduling. Reusable code currently under `src/portfolio_agent` can later be moved or renamed; it is not an additional business agent.

Core values should be represented explicitly:

```python
@dataclass(frozen=True)
class Portfolio:
    cash: Decimal
    quantities: dict[int, Decimal]

@dataclass(frozen=True)
class TargetWeight:
    instrument_id: int
    sanitized_weight: Decimal

@dataclass(frozen=True)
class SimulatedTrade:
    instrument_id: int
    side: str
    shares_before: Decimal
    target_shares: Decimal
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    fee: Decimal
    cash_change: Decimal
```

Production money calculations should use `Decimal` or fixed-precision database types. The current SQLite schema uses `REAL` for prototype convenience; a PostgreSQL migration should use appropriately sized `NUMERIC` columns.

## 3. Static setup

### 3.1 Team

Insert one `teams` row:

```text
id              generated primary key
team_code       stable unique external identifier: team_alpha
display_name    Team Alpha
api_key_hash    unique secure API-key hash; never plaintext
status          ACTIVE
created_at      UTC insertion time
updated_at      UTC update time
```

```python
team_id = team_repository.create_team(
    team_code="team_alpha",
    display_name="Team Alpha",
    api_key_hash=hash_api_key(plain_key),
)
```

### 3.2 Instruments

Insert every tradable stock into `instruments`:

```text
id              stable database key referenced by business tables
ticker          unique ticker, for example AAPL
company_name    Apple Inc.
sector          Technology
exchange        NASDAQ
currency        USD
is_active       1
created_at      UTC insertion time
```

The server must validate client tickers against active database instruments:

```python
instruments = instrument_repository.list_active()
instrument_by_ticker = {row.ticker: row for row in instruments}
```

## 4. Trading-day creation and state

The calendar job inserts one row for 2026-09-01 and one for the next valid session, 2026-09-02. A trading day is inserted once and its coordination fields are updated; a new row is not created for every transition.

`trading_days` fields:

```text
id                         primary key
trading_date               unique exchange-local market date
market_open_at             UTC open derived from the exchange calendar
market_close_at            UTC close derived from the exchange calendar
submission_open_at         set when observations are published
submission_deadline_at     fixed cutoff before the next valid open
market_status              PENDING -> DATA_IMPORTED/FAILED
execution_status           PENDING -> PROCESSING -> COMPLETED/FAILED
valuation_status           PENDING -> PROCESSING -> COMPLETED/FAILED
observation_status         PENDING -> GENERATING -> PUBLISHED/FAILED
created_at/updated_at      UTC timestamps
```

The `observations` row itself has no ambiguous status. `trading_days.observation_status` is only orchestration state. Every workflow transition is also appended to `audit_logs`.

## 5. Import the complete day-T data

After the close, the collector imports one `market_bars` row per active instrument:

```text
id                  primary key
trading_day_id      foreign key for 2026-09-01
instrument_id       instrument foreign key
open/high/low/close raw daily prices
adjusted_open       execution price source
adjusted_close      close valuation price source
volume              daily volume
source              provider name
received_at         provider arrival time
created_at          database insertion time
```

The import is atomic. Only after all active instruments pass completeness and quality checks does the application set `market_status = DATA_IMPORTED`. A partial import is rolled back, marked failed, and audited.

Point-in-time fundamentals are stored in `fundamental_records`. `period_end` is the reporting period, `available_at` is the earliest permitted observation time, and `payload_json` retains the exact provider record. There is no news table because the organizer does not send news.

## 6. Day-T close portfolio and observation

Assume the 2026-09-01 close portfolio is:

```text
cash                 400,000
AAPL                  2,000 shares x 180 = 360,000
MSFT                    800 shares x 300 = 240,000
positions value                            600,000
NAV                                      1,000,000
```

Insert one `portfolio_snapshots` header:

```text
team_id                         team_alpha
trading_day_id                  2026-09-01
execution_id                    that day's execution ID, or NULL
snapshot_type                   CLOSE
cash                            400000
positions_value                 600000
nav                             1000000
gross_exposure                  0.60
drawdown                        calculated from historical peak NAV
effective_at                    market close
created_at                      actual insertion time
```

Insert child `position_snapshots` rows:

```text
portfolio_snapshot_id           the CLOSE snapshot ID
instrument_id                   AAPL/MSFT
quantity                        2000 / 800
reference_price                 180 / 300
market_value                    360000 / 240000
weight                          0.36 / 0.24
created_at                      insertion time
```

The header and its position rows together are one portfolio. They are separate relational tables only because one portfolio contains many positions.

Build the payload from stored inputs:

```python
payload = observation_builder.build(
    signal_date="2026-09-01",
    market_history=market_repository.history_through(day_id),
    fundamentals=fundamental_repository.available_at(market_close_at),
    portfolio=portfolio_repository.load_snapshot(close_snapshot_id),
    constraints=current_competition_constraints,
)
```

The payload contains no news:

```json
{
  "type": "decision_request",
  "protocol_version": "1.0",
  "session_date": "2026-09-01",
  "submission_deadline_at": "2026-09-02T13:29:59Z",
  "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "NVDA"}],
  "market_features": {},
  "fundamental_features": {},
  "portfolio": {
    "cash": 400000,
    "nav": 1000000,
    "weights": {"AAPL": 0.36, "MSFT": 0.24}
  },
  "constraints": {}
}
```

Persist the exact served document in `observations`:

```text
id                          primary key
team_id                     team_alpha
trading_day_id              2026-09-01
close_portfolio_snapshot_id source CLOSE snapshot
payload_json                exact document
payload_hash                SHA-256 of canonical JSON
generated_at                build completion time
published_at                availability time, NULL before publication
first_served_at             set once on the first successful GET
created_at                  insertion time
```

## 7. One participant submission

The API derives the authoritative team from the API key and loads the published observation. The participant sends:

```json
{
  "type": "decision_response",
  "protocol_version": "1.0",
  "session_date": "2026-09-01",
  "target_weights": {
    "AAPL": 0.10,
    "MSFT": 0.08,
    "NVDA": 0.07
  },
  "metadata": {"agent_version": "alpha-3.1"}
}
```

The service uses two explicit transactions for receipt and processing:

```python
def receive_decision(authenticated_team_id, body, idempotency_key, server_now):
    # 1. Load the observation/signal day and serialize team+day receipt.
    # 2. Return the stored receipt when the idempotency key already exists.
    # 3. Reject and audit a different second submission for team+signal day.
    # 4. INSERT the raw submission as RECEIVED and RETURNING id.
    # 5. Commit the receipt; the generated id is the submission_id.
    # 6. Call process_submission_weights(submission_id).
    # 7. Return the final stored receipt.
```

Every `decision_submissions` field:

```text
id                       primary key
team_id                  authenticated team, never trusted from client metadata
observation_id           exact observation used by the decision
signal_day_id            2026-09-01
execution_day_id         explicit next valid day, 2026-09-02
idempotency_key          client request key
received_at              authoritative server receipt time
raw_payload_json         untouched request document
payload_hash             canonical JSON SHA-256
source                   PARTICIPANT, or FALLBACK for server policy
status                   RECEIVED/VALIDATED/REJECTED/QUEUED/EXECUTED
rejection_reason         required only for REJECTED
validator_version        validator implementation version
validation_policy_json   exact constraint/policy snapshot used
validation_summary_json  portfolio-level validation summary
expected_weight_count    active-instrument count frozen at receipt
stored_weight_count      normalized weight rows successfully stored
sanitized_gross_weight   sum of the final target vector
weights_processed_at     weight-processing completion time
agent_version            participant metadata
created_at/updated_at    UTC timestamps
```

`UNIQUE(team_id, signal_day_id)` enforces one submission. The same idempotency key retrieves the original outcome; it does not create another row.

### 7.1 Exact origin of `submission_id`

The database generates it. The client does not send it and application code does not predict it:

```sql
INSERT INTO decision_submissions (
    team_id, observation_id, signal_day_id, execution_day_id,
    idempotency_key, received_at, raw_payload_json, payload_hash,
    source, status, validator_version, validation_policy_json,
    expected_weight_count, stored_weight_count, created_at, updated_at
)
VALUES (
    :team_id, :observation_id, :signal_day_id, :execution_day_id,
    :idempotency_key, :received_at, :raw_json, :payload_hash,
    'PARTICIPANT', 'RECEIVED', :validator_version, :policy_json,
    :active_count, 0, :now, :now
)
RETURNING id;
```

If the database returns `id=200`, every normalized weight row uses `submission_id=200`. The receipt commits first, so a validator crash leaves an auditable `RECEIVED` submission that can be resumed.

## 8. Normalize and store weights

Weights are persisted after raw receipt and before execution creation. A submission processor, not the execution engine, owns this step:

```python
def process_submission_weights(submission_id: int):
    with database.transaction():
        submission = submissions.lock_received(submission_id)
        active = instruments.list_active()
        raw = parse_target_weights(submission.raw_payload_json)
        result = weight_validator.validate(
            raw_weights=raw,
            instruments=active,
            policy=submission.validation_policy_json,
        )
        submission_weights.bulk_insert(
            submission_id=submission.id,
            rows=result.complete_instrument_vector,
        )
        assert submission_weights.count(submission.id) == submission.expected_weight_count
        submissions.mark_queued(
            submission.id,
            stored_weight_count=len(result.complete_instrument_vector),
            sanitized_gross_weight=sum(x.sanitized_weight for x in result.complete_instrument_vector),
            weights_processed_at=now_utc(),
            validation_summary=result.summary,
        )
        executions.create_pending_from_submission(submission)
```

This processing transaction either commits completely or rolls back completely. After rollback, the separately committed raw submission remains `RECEIVED`, so a recovery worker can process it again by ID.

Insert one `submission_weights` row for every active instrument, not only the three submitted tickers:

```text
ticker  was_provided  raw_weight  sanitized_weight  validation_codes_json
AAPL    1             0.10        0.10              []
MSFT    1             0.08        0.08              []
NVDA    1             0.07        0.07              []
JPM     0             NULL        0.00              []
```

Sanitization rules may evolve, but the database interface remains stable: validation emits one final numeric target per active instrument and freezes `validator_version` plus `validation_policy_json`.

Execution loads the complete vector in one set query, not one query per asset:

```sql
SELECT instrument_id, sanitized_weight
FROM submission_weights
WHERE submission_id = ?
ORDER BY instrument_id;
```

A target does not prove that a trade occurred. Actual buying and selling is queried from `transactions`. Convenience views expose denormalized weight and transaction histories.

### 8.1 Strict engine input contract

The execution service accepts only a `QUEUED` submission whose `stored_weight_count` equals `expected_weight_count`. It assembles:

```python
ExecutionInput(
    execution_id=300,
    submission_id=200,
    team_id=team_alpha_id,
    signal_day_id=day_2026_09_01_id,
    execution_day_id=day_2026_09_02_id,
    prior_portfolio_snapshot_id=close_snapshot_id,
    targets=[
        TargetWeight(instrument_id=aapl_id, sanitized_weight=Decimal("0.10")),
        TargetWeight(instrument_id=msft_id, sanitized_weight=Decimal("0.08")),
        TargetWeight(instrument_id=nvda_id, sanitized_weight=Decimal("0.07")),
        # Every other active instrument is also present with weight zero.
    ],
    open_prices={aapl_id: Decimal("182"), msft_id: Decimal("302"), nvda_id: Decimal("120")},
    fee_rate=Decimal("0.001"),
    engine_version="execution-v1",
)
```

The engine never reads `raw_payload_json`, understands no HTTP envelope, and does not sanitize again. It only computes from `sanitized targets + prior portfolio + execution-day open prices`. This is the single stable weight contract between database storage and execution.

## 9. Schedule execution

After the complete vector is stored and counted, the weight-processing transaction inserts one `executions` row:

```text
id                    primary key
team_id               team_alpha
submission_id         unique source submission
trading_day_id        2026-09-02
status                PENDING
engine_version        execution implementation version
scheduled_at          T+1 economic open
effective_at          NULL until completion, then T+1 open
processed_at          NULL until the server processes it
nav_before            pre-trade opening NAV
cash_before/after     cash around execution
total_buy_value       aggregate purchases
total_sell_value      aggregate sales
total_fee             aggregate fees
error_code/message    populated on failure
created_at/updated_at UTC timestamps
```

Unique constraints enforce one execution per submission and one execution per team/day.

## 10. Process T+1 after the close

After importing the complete 2026-09-02 bars, the orchestrator selects pending work:

```sql
SELECT * FROM executions
WHERE trading_day_id = :day_id AND status = 'PENDING';
```

For `team_alpha` it:

1. loads the latest valid portfolio, the 2026-09-01 CLOSE snapshot;
2. loads the complete sanitized target for the execution's submission;
3. loads 2026-09-02 adjusted open prices;
4. marks the execution `PROCESSING`;
5. calls the pure execution engine;
6. atomically writes transactions, cash entries, and the POST_OPEN portfolio;
7. marks the execution `COMPLETED`.

Example open prices:

```text
AAPL 182
MSFT 302
NVDA 120
```

Pre-trade opening NAV:

```text
400,000 + 2,000x182 + 800x302 = 1,005,600
```

Target quantities:

```text
AAPL = 1,005,600 x 0.10 / 182
MSFT = 1,005,600 x 0.08 / 302
NVDA = 1,005,600 x 0.07 / 120
```

Every changed instrument creates one `transactions` row:

```text
id                 primary key
execution_id       parent execution batch
instrument_id      traded instrument
side               BUY/SELL
shares_before      quantity before execution
target_shares      desired post-execution quantity
quantity           absolute quantity change
price              T+1 adjusted open
gross_amount       quantity x price
fee                transaction fee
cash_change        net cash effect; negative buy, positive sell
status             COMPLETED/SKIPPED/FAILED
effective_at       T+1 open
processed_at       actual batch time
created_at         insertion time
```

One deterministic simulated fill per instrument means a single `transactions` table is sufficient. One execution naturally owns many transactions through `execution_id`.

## 11. Cash and POST_OPEN portfolio

Append every cash movement to `cash_ledger`:

```text
id                 primary key
team_id            team_alpha
trading_day_id     2026-09-02
execution_id       current execution, nullable for non-execution events
transaction_id     related trade, nullable for initial capital/adjustments
event_type         INITIAL_CAPITAL/TRADE/FEE/DIVIDEND/ADJUSTMENT
amount             signed movement
balance_before     balance before movement
balance_after      balance after movement
effective_at       economic time
created_at         insertion time
```

`transactions.cash_change` explains the net effect of one market trade. `cash_ledger` proves complete ordered cash continuity and includes events without a transaction.

After trading, insert a `POST_OPEN` portfolio header whose cash equals the last ledger balance and whose position value uses open prices. Insert all nonzero child positions. Execution, transactions, cash ledger, and POST_OPEN portfolio must commit in one transaction or all roll back.

## 12. T+1 close valuation and performance

Example closes:

```text
AAPL 184
MSFT 305
NVDA 123
```

```python
close_result = valuation_engine.value(
    portfolio=post_open_portfolio,
    close_prices=market_repository.close_prices(day_id),
)
```

Insert the `CLOSE` portfolio and positions, then `daily_performance`:

```text
id                          primary key
team_id                     team_alpha
trading_day_id              2026-09-02
close_portfolio_snapshot_id unique CLOSE snapshot reference
previous_close_nav          2026-09-01 CLOSE NAV
current_close_nav           2026-09-02 CLOSE NAV
daily_return                current / previous - 1
cumulative_return           current / initial capital - 1
transaction_cost            execution fees charged that day
turnover                    traded notional / defined NAV denominator
drawdown                    current / historical peak - 1
calculated_at               calculation time
created_at                  insertion time
```

`daily_performance` is a summary, not a ledger. It can be recalculated from snapshots, transactions, and cash entries. Leaderboard metrics consume these daily summaries or the underlying facts. The next observation is then built from complete 2026-09-02 data and the CLOSE portfolio.

## 13. State, audit, and idempotency

Facts are append-only: market bars, observations, submissions, weights, transactions, cash entries, snapshots, and performance are never silently overwritten. Coordination status is mutable on trading days, submissions, and executions.

Each important transition appends `audit_logs`:

```text
id                 primary key
team_id/day_id     related scope, nullable
actor_type         SYSTEM/TEAM/ADMIN
actor_id           caller identity
event_type         e.g. SUBMISSION_ACCEPTED, EXECUTION_COMPLETED
entity_type/id     logical affected entity
request_id         correlated HTTP request or job ID
details_json       structured details
created_at         UTC timestamp
```

End-of-day retry logic selects only `PENDING` work or deliberately recovered `PROCESSING` work. A `COMPLETED` execution cannot create transactions or charge fees twice.

## 14. Failure behavior supported by the schema

- Same HTTP retry: return the original result by idempotency key.
- Different second submission: reject and audit; never create a replacement submission.
- Missing submission: create one `source=FALLBACK` decision and execution at the deadline.
- Invalid weights: retain the raw JSON and store normalized processing results.
- Per-asset skipped/failed simulation: transaction status and execution error fields.
- Batch failure: roll back business writes and leave recoverable execution state.
- Duplicate settlement protection: unique submission, execution, snapshot, and performance keys.
- Lifecycle tracing: current state on business rows, every transition in the audit log.

Specific validation, missing-price, suspension, corporate-action, and fallback policies are business rules. Version, status, error, and raw-input columns allow those rules to evolve without losing historical explainability.

## 15. End-to-end chain

```text
Import complete T bars
  -> execute T-1 submission at T open (processed after close)
  -> transactions + cash ledger
  -> POST_OPEN portfolio + positions
  -> value at T close
  -> CLOSE portfolio + positions
  -> daily performance
  -> build/store/publish T observation (no news)
  -> participant submits once
  -> raw submission + normalized/sanitized weights
  -> create PENDING T+1 execution
  -> repeat after T+1 close
```

Every transaction can be traced backward to its execution, submission, observation, signal day, execution day, and team. Every daily performance row can be traced to its CLOSE portfolio, positions, market bars, transactions, and cash movements.
