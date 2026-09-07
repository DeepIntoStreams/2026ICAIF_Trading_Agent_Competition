-- Add an explicit predecessor to every persisted POST_OPEN/CLOSE snapshot.
-- INITIAL snapshots are roots and therefore keep a NULL predecessor.

ALTER TABLE portfolio_snapshots
    ADD COLUMN IF NOT EXISTS prior_close_snapshot_id BIGINT;

-- Exact backfill for snapshots produced by an execution. The decision's immutable
-- observation is the authoritative source of the state used by that execution.
UPDATE portfolio_snapshots child
   SET prior_close_snapshot_id = source.close_portfolio_snapshot_id
  FROM executions execution
  JOIN decision_submissions submission
    ON submission.id = execution.submission_id
  JOIN observations source
    ON source.id = submission.observation_id
 WHERE child.execution_id = execution.id
   AND child.snapshot_type IN ('POST_OPEN', 'CLOSE')
   AND child.prior_close_snapshot_id IS NULL;

-- Legacy/bootstrap CLOSE snapshots may predate an execution. Link those rows to
-- the nearest earlier INITIAL/CLOSE state for the same team. Production execution
-- snapshots never use this fallback; their lineage is provided by the query above.
WITH legacy_predecessors AS (
    SELECT child.id AS child_id, predecessor.id AS predecessor_id
      FROM portfolio_snapshots child
      JOIN trading_days child_day ON child_day.id = child.trading_day_id
      JOIN LATERAL (
          SELECT candidate.id
            FROM portfolio_snapshots candidate
            LEFT JOIN trading_days candidate_day
              ON candidate_day.id = candidate.trading_day_id
           WHERE candidate.team_id = child.team_id
             AND candidate.id <> child.id
             AND candidate.snapshot_type IN ('INITIAL', 'CLOSE')
             AND (
                 candidate_day.trading_date < child_day.trading_date
                 OR (
                     candidate_day.trading_date = child_day.trading_date
                     AND candidate.effective_at < child.effective_at
                 )
             )
           ORDER BY candidate_day.trading_date DESC NULLS LAST,
                    candidate.effective_at DESC,
                    candidate.id DESC
           LIMIT 1
      ) predecessor ON TRUE
     WHERE child.snapshot_type IN ('POST_OPEN', 'CLOSE')
       AND child.execution_id IS NULL
       AND child.prior_close_snapshot_id IS NULL
)
UPDATE portfolio_snapshots child
   SET prior_close_snapshot_id = legacy.predecessor_id
  FROM legacy_predecessors legacy
 WHERE child.id = legacy.child_id;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM portfolio_snapshots
         WHERE snapshot_type IN ('POST_OPEN', 'CLOSE')
           AND prior_close_snapshot_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'cannot migrate snapshot lineage: non-INITIAL snapshots without a predecessor remain';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM portfolio_snapshots child
          JOIN portfolio_snapshots predecessor
            ON predecessor.id = child.prior_close_snapshot_id
          LEFT JOIN trading_days child_day ON child_day.id = child.trading_day_id
          LEFT JOIN trading_days predecessor_day
            ON predecessor_day.id = predecessor.trading_day_id
         WHERE predecessor.team_id <> child.team_id
            OR predecessor.snapshot_type NOT IN ('INITIAL', 'CLOSE')
            OR predecessor.id = child.id
            OR predecessor_day.trading_date > child_day.trading_date
            OR (
                predecessor_day.trading_date = child_day.trading_date
                AND predecessor.effective_at >= child.effective_at
            )
    ) THEN
        RAISE EXCEPTION
            'cannot migrate snapshot lineage: invalid team, type, or self-reference';
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS portfolio_snapshots_by_prior_close
    ON portfolio_snapshots(prior_close_snapshot_id);

CREATE INDEX IF NOT EXISTS cash_ledger_by_transaction
    ON cash_ledger(transaction_id)
    WHERE transaction_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'portfolio_snapshots_prior_close_fkey'
    ) THEN
        ALTER TABLE portfolio_snapshots
            ADD CONSTRAINT portfolio_snapshots_prior_close_fkey
            FOREIGN KEY (prior_close_snapshot_id)
            REFERENCES portfolio_snapshots(id)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'portfolio_snapshots_prior_close_shape'
    ) THEN
        ALTER TABLE portfolio_snapshots
            ADD CONSTRAINT portfolio_snapshots_prior_close_shape
            CHECK (
                (snapshot_type = 'INITIAL' AND prior_close_snapshot_id IS NULL)
                OR
                (snapshot_type IN ('POST_OPEN', 'CLOSE')
                 AND prior_close_snapshot_id IS NOT NULL)
            ) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'portfolio_snapshots_prior_close_not_self'
    ) THEN
        ALTER TABLE portfolio_snapshots
            ADD CONSTRAINT portfolio_snapshots_prior_close_not_self
            CHECK (
                prior_close_snapshot_id IS NULL
                OR prior_close_snapshot_id <> id
            ) NOT VALID;
    END IF;
END
$$;

ALTER TABLE portfolio_snapshots
    VALIDATE CONSTRAINT portfolio_snapshots_prior_close_fkey;
ALTER TABLE portfolio_snapshots
    VALIDATE CONSTRAINT portfolio_snapshots_prior_close_shape;
ALTER TABLE portfolio_snapshots
    VALIDATE CONSTRAINT portfolio_snapshots_prior_close_not_self;

-- Competition's persistence API does not know this Deployment-owned column.
-- Populate and validate it transparently before the unchanged INSERT is checked.
CREATE OR REPLACE FUNCTION deployment_set_snapshot_prior_close()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_prior_id BIGINT;
    predecessor_team_id BIGINT;
    predecessor_type TEXT;
    predecessor_date DATE;
    child_date DATE;
    predecessor_effective_at TIMESTAMPTZ;
BEGIN
    IF NEW.snapshot_type = 'INITIAL' THEN
        IF NEW.prior_close_snapshot_id IS NOT NULL THEN
            RAISE EXCEPTION 'INITIAL snapshot cannot have a prior CLOSE snapshot';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.execution_id IS NULL THEN
        RAISE EXCEPTION '% snapshot requires an execution_id', NEW.snapshot_type;
    END IF;

    SELECT observation.close_portfolio_snapshot_id
      INTO expected_prior_id
      FROM executions execution
      JOIN decision_submissions submission
        ON submission.id = execution.submission_id
      JOIN observations observation
        ON observation.id = submission.observation_id
     WHERE execution.id = NEW.execution_id
       AND execution.team_id = NEW.team_id;

    IF expected_prior_id IS NULL THEN
        RAISE EXCEPTION
            'cannot resolve prior CLOSE snapshot for execution %', NEW.execution_id;
    END IF;

    IF NEW.prior_close_snapshot_id IS NULL THEN
        NEW.prior_close_snapshot_id := expected_prior_id;
    ELSIF NEW.prior_close_snapshot_id <> expected_prior_id THEN
        RAISE EXCEPTION
            'snapshot prior % differs from execution source observation prior %',
            NEW.prior_close_snapshot_id, expected_prior_id;
    END IF;

    SELECT predecessor.team_id, predecessor.snapshot_type,
           predecessor_day.trading_date, predecessor.effective_at,
           child_day.trading_date
      INTO predecessor_team_id, predecessor_type, predecessor_date,
           predecessor_effective_at, child_date
      FROM portfolio_snapshots predecessor
      LEFT JOIN trading_days predecessor_day
        ON predecessor_day.id = predecessor.trading_day_id
      LEFT JOIN trading_days child_day
        ON child_day.id = NEW.trading_day_id
     WHERE predecessor.id = NEW.prior_close_snapshot_id;

    IF predecessor_team_id IS NULL
       OR predecessor_team_id <> NEW.team_id
       OR predecessor_type NOT IN ('INITIAL', 'CLOSE') THEN
        RAISE EXCEPTION
            'snapshot prior % has an invalid team or type', NEW.prior_close_snapshot_id;
    END IF;

    IF predecessor_date IS NULL
       OR child_date IS NULL
       OR predecessor_date > child_date
       OR (predecessor_date = child_date
           AND predecessor_effective_at >= NEW.effective_at) THEN
        RAISE EXCEPTION
            'snapshot prior % is not earlier than the new snapshot',
            NEW.prior_close_snapshot_id;
    END IF;

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS set_snapshot_prior_close ON portfolio_snapshots;
CREATE TRIGGER set_snapshot_prior_close
BEFORE INSERT OR UPDATE OF execution_id, prior_close_snapshot_id, snapshot_type, team_id
ON portfolio_snapshots
FOR EACH ROW
EXECUTE FUNCTION deployment_set_snapshot_prior_close();

CREATE OR REPLACE VIEW v_portfolio_positions AS
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
    ps.effective_at,
    ps.prior_close_snapshot_id
FROM portfolio_snapshots ps
JOIN teams t ON t.id = ps.team_id
LEFT JOIN trading_days td ON td.id = ps.trading_day_id
LEFT JOIN position_snapshots pos ON pos.portfolio_snapshot_id = ps.id
LEFT JOIN instruments i ON i.id = pos.instrument_id;
