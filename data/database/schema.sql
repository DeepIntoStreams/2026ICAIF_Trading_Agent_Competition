PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    team_code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SUSPENDED', 'DISQUALIFIED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    sector TEXT,
    exchange TEXT NOT NULL DEFAULT 'NYSE/NASDAQ',
    currency TEXT NOT NULL DEFAULT 'USD',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trading_days (
    id INTEGER PRIMARY KEY,
    trading_date TEXT NOT NULL UNIQUE,
    market_open_at TEXT NOT NULL,
    market_close_at TEXT NOT NULL,
    submission_open_at TEXT,
    submission_deadline_at TEXT,
    market_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (market_status IN ('PENDING', 'DATA_IMPORTED', 'FAILED')),
    execution_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (execution_status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    valuation_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (valuation_status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    observation_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (observation_status IN ('PENDING', 'GENERATING', 'PUBLISHED', 'FAILED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        submission_open_at IS NULL OR submission_deadline_at IS NULL
        OR submission_open_at < submission_deadline_at
    )
);

CREATE TABLE IF NOT EXISTS market_bars (
    id INTEGER PRIMARY KEY,
    trading_day_id INTEGER NOT NULL REFERENCES trading_days(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    open REAL NOT NULL CHECK (open > 0),
    high REAL NOT NULL CHECK (high > 0),
    low REAL NOT NULL CHECK (low > 0),
    close REAL NOT NULL CHECK (close > 0),
    adjusted_open REAL NOT NULL CHECK (adjusted_open > 0),
    adjusted_close REAL NOT NULL CHECK (adjusted_close > 0),
    volume REAL NOT NULL CHECK (volume >= 0),
    source TEXT NOT NULL,
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (trading_day_id, instrument_id),
    CHECK (high >= low),
    CHECK (high >= open AND high >= close),
    CHECK (low <= open AND low <= close)
);

CREATE INDEX IF NOT EXISTS market_bars_by_instrument_day
    ON market_bars(instrument_id, trading_day_id);

CREATE TABLE IF NOT EXISTS fundamental_records (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    period_end TEXT NOT NULL,
    available_at TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (instrument_id, period_end, source)
);

CREATE INDEX IF NOT EXISTS fundamentals_point_in_time
    ON fundamental_records(instrument_id, available_at);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    trading_day_id INTEGER REFERENCES trading_days(id),
    execution_id INTEGER REFERENCES executions(id),
    snapshot_type TEXT NOT NULL
        CHECK (snapshot_type IN ('INITIAL', 'POST_OPEN', 'CLOSE')),
    cash REAL NOT NULL,
    positions_value REAL NOT NULL,
    nav REAL NOT NULL CHECK (nav >= 0),
    gross_exposure REAL NOT NULL DEFAULT 0 CHECK (gross_exposure >= 0),
    drawdown REAL NOT NULL DEFAULT 0,
    effective_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (team_id, trading_day_id, snapshot_type)
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY,
    portfolio_snapshot_id INTEGER NOT NULL
        REFERENCES portfolio_snapshots(id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    quantity REAL NOT NULL,
    reference_price REAL NOT NULL CHECK (reference_price > 0),
    market_value REAL NOT NULL,
    weight REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (portfolio_snapshot_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS positions_by_instrument
    ON position_snapshots(instrument_id, portfolio_snapshot_id);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    trading_day_id INTEGER NOT NULL REFERENCES trading_days(id),
    close_portfolio_snapshot_id INTEGER NOT NULL
        REFERENCES portfolio_snapshots(id),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    published_at TEXT,
    first_served_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (team_id, trading_day_id)
);

CREATE INDEX IF NOT EXISTS observations_for_team_day
    ON observations(team_id, trading_day_id);

CREATE TABLE IF NOT EXISTS decision_submissions (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    observation_id INTEGER NOT NULL REFERENCES observations(id),
    signal_day_id INTEGER NOT NULL REFERENCES trading_days(id),
    execution_day_id INTEGER NOT NULL REFERENCES trading_days(id),
    idempotency_key TEXT NOT NULL,
    received_at TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL CHECK (json_valid(raw_payload_json)),
    payload_hash TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'PARTICIPANT'
        CHECK (source IN ('PARTICIPANT', 'FALLBACK')),
    status TEXT NOT NULL
        CHECK (status IN ('RECEIVED', 'VALIDATED', 'REJECTED', 'QUEUED', 'EXECUTED')),
    rejection_reason TEXT,
    validator_version TEXT NOT NULL,
    validation_policy_json TEXT NOT NULL CHECK (json_valid(validation_policy_json)),
    validation_summary_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(validation_summary_json)),
    expected_weight_count INTEGER NOT NULL CHECK (expected_weight_count >= 0),
    stored_weight_count INTEGER NOT NULL DEFAULT 0 CHECK (stored_weight_count >= 0),
    sanitized_gross_weight REAL,
    weights_processed_at TEXT,
    agent_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (team_id, signal_day_id),
    UNIQUE (team_id, idempotency_key),
    CHECK (signal_day_id <> execution_day_id),
    CHECK (
        (status = 'REJECTED' AND rejection_reason IS NOT NULL)
        OR status <> 'REJECTED'
    ),
    CHECK (stored_weight_count <= expected_weight_count),
    CHECK (
        status IN ('RECEIVED', 'REJECTED')
        OR (
            stored_weight_count = expected_weight_count
            AND sanitized_gross_weight IS NOT NULL
            AND weights_processed_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS submissions_for_execution_day
    ON decision_submissions(execution_day_id, status);

CREATE TABLE IF NOT EXISTS submission_weights (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL
        REFERENCES decision_submissions(id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    was_provided INTEGER NOT NULL CHECK (was_provided IN (0, 1)),
    raw_weight REAL,
    sanitized_weight REAL NOT NULL,
    validation_codes_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(validation_codes_json)),
    created_at TEXT NOT NULL,
    UNIQUE (submission_id, instrument_id),
    CHECK (was_provided = 1 OR raw_weight IS NULL)
);

CREATE INDEX IF NOT EXISTS weights_for_submission
    ON submission_weights(submission_id, instrument_id);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    submission_id INTEGER NOT NULL UNIQUE REFERENCES decision_submissions(id),
    trading_day_id INTEGER NOT NULL REFERENCES trading_days(id),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    engine_version TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    effective_at TEXT,
    processed_at TEXT,
    nav_before REAL,
    cash_before REAL,
    cash_after REAL,
    total_buy_value REAL NOT NULL DEFAULT 0,
    total_sell_value REAL NOT NULL DEFAULT 0,
    total_fee REAL NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (team_id, trading_day_id)
);

CREATE INDEX IF NOT EXISTS pending_executions
    ON executions(trading_day_id, status);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    execution_id INTEGER NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    shares_before REAL NOT NULL,
    target_shares REAL NOT NULL,
    quantity REAL NOT NULL CHECK (quantity >= 0),
    price REAL NOT NULL CHECK (price > 0),
    gross_amount REAL NOT NULL CHECK (gross_amount >= 0),
    fee REAL NOT NULL CHECK (fee >= 0),
    cash_change REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'SKIPPED', 'FAILED')),
    effective_at TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (execution_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS transactions_by_instrument
    ON transactions(instrument_id, execution_id);

CREATE TABLE IF NOT EXISTS cash_ledger (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    trading_day_id INTEGER REFERENCES trading_days(id),
    execution_id INTEGER REFERENCES executions(id),
    transaction_id INTEGER REFERENCES transactions(id),
    event_type TEXT NOT NULL
        CHECK (event_type IN ('INITIAL_CAPITAL', 'TRADE', 'FEE', 'DIVIDEND', 'ADJUSTMENT')),
    amount REAL NOT NULL,
    balance_before REAL NOT NULL,
    balance_after REAL NOT NULL,
    effective_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS cash_ledger_for_team
    ON cash_ledger(team_id, effective_at, id);

CREATE TABLE IF NOT EXISTS daily_performance (
    id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    trading_day_id INTEGER NOT NULL REFERENCES trading_days(id),
    close_portfolio_snapshot_id INTEGER NOT NULL UNIQUE
        REFERENCES portfolio_snapshots(id),
    previous_close_nav REAL NOT NULL CHECK (previous_close_nav >= 0),
    current_close_nav REAL NOT NULL CHECK (current_close_nav >= 0),
    daily_return REAL NOT NULL,
    cumulative_return REAL NOT NULL,
    transaction_cost REAL NOT NULL DEFAULT 0 CHECK (transaction_cost >= 0),
    turnover REAL NOT NULL DEFAULT 0 CHECK (turnover >= 0),
    drawdown REAL NOT NULL,
    calculated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (team_id, trading_day_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY,
    team_id INTEGER REFERENCES teams(id),
    trading_day_id INTEGER REFERENCES trading_days(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('SYSTEM', 'TEAM', 'ADMIN')),
    actor_id TEXT,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    request_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_by_day_type
    ON audit_logs(trading_day_id, event_type, created_at);

CREATE VIEW IF NOT EXISTS v_submission_weight_details AS
SELECT
    ds.id AS submission_id,
    t.team_code,
    signal_day.trading_date AS signal_date,
    execution_day.trading_date AS execution_date,
    i.ticker,
    sw.was_provided,
    sw.raw_weight,
    sw.sanitized_weight,
    sw.validation_codes_json,
    ds.status AS submission_status
FROM submission_weights sw
JOIN decision_submissions ds ON ds.id = sw.submission_id
JOIN teams t ON t.id = ds.team_id
JOIN trading_days signal_day ON signal_day.id = ds.signal_day_id
JOIN trading_days execution_day ON execution_day.id = ds.execution_day_id
JOIN instruments i ON i.id = sw.instrument_id;

CREATE VIEW IF NOT EXISTS v_transaction_history AS
SELECT
    tx.id AS transaction_id,
    e.id AS execution_id,
    ds.id AS submission_id,
    t.team_code,
    td.trading_date,
    i.ticker,
    tx.side,
    tx.quantity,
    tx.price,
    tx.gross_amount,
    tx.fee,
    tx.cash_change,
    tx.status,
    tx.effective_at,
    tx.processed_at
FROM transactions tx
JOIN executions e ON e.id = tx.execution_id
JOIN decision_submissions ds ON ds.id = e.submission_id
JOIN teams t ON t.id = e.team_id
JOIN trading_days td ON td.id = e.trading_day_id
JOIN instruments i ON i.id = tx.instrument_id;

CREATE VIEW IF NOT EXISTS v_portfolio_positions AS
SELECT
    ps.id AS portfolio_snapshot_id,
    t.team_code,
    td.trading_date,
    ps.snapshot_type,
    ps.cash,
    ps.positions_value,
    ps.nav,
    i.ticker,
    pos.quantity,
    pos.reference_price,
    pos.market_value,
    pos.weight,
    ps.effective_at
FROM portfolio_snapshots ps
JOIN teams t ON t.id = ps.team_id
LEFT JOIN trading_days td ON td.id = ps.trading_day_id
LEFT JOIN position_snapshots pos ON pos.portfolio_snapshot_id = ps.id
LEFT JOIN instruments i ON i.id = pos.instrument_id;
