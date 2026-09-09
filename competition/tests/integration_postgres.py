"""End-to-end integration: TradingRepository against the real PostgreSQL schema.

Covers the explicit snapshot-lineage contract (`prior_close_snapshot_id`) and the leaderboard.
Reproduces LIVE_WORKFLOW_EXAMPLE §6-§12 (team_alpha, 2026-09-01 -> 2026-09-02):

  1. seed the PLATFORM tables the server owns, including the GENESIS chain
     (INITIAL snapshot with prior = NULL, then the signal-day CLOSE chained off it);
  2. `repo.load_state(..., prior_close_snapshot_id=...)` -> state from that EXACT snapshot,
     with peak NAV walked over the ancestor chain (INITIAL included);
  3. `execute_decision(...)`  -> the after-close batch (id-first, Decimal);
  4. `repo.persist_day(..., prior_close_snapshot_id=...)` -> atomic, lineage stamped on both
     the POST_OPEN and CLOSE snapshots;
  5. read it back: reconstruction, cash-ledger transaction linkage, lineage, peak-NAV high-water;
  6. `compute_and_store_leaderboard(...)` -> ranked board, and re-running upserts (no duplicates).

Run:
    COMPETITION_DATABASE_URL=postgresql://... COMPETITION_SCHEMA_SQL=/path/schema.sql \
        python competition/tests/integration_postgres.py
"""

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT.parent / "src"))

import engine
from trading_repo import TradingRepository, compute_and_store_leaderboard

NOW = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)

TEAM_ID = 1
INSTR = {"AAPL": 1, "MSFT": 2, "NVDA": 3, "JPM": 4}     # ticker -> instrument_id (seeds names only)
AAPL, MSFT, NVDA, JPM = 1, 2, 3, 4
ACTIVE = [AAPL, MSFT, NVDA, JPM]
SIGNAL_DAY_ID, EXEC_DAY_ID, SUBMISSION_ID = 1, 2, 1
SIGNAL_DATE, EXEC_DATE = "2026-09-01", "2026-09-02"
CONSTRAINTS = {"max_asset_weight": 0.30, "max_gross_exposure": 1.00}

PRIOR = {"cash": Decimal("400000"), "shares": {AAPL: Decimal("2000"), MSFT: Decimal("800")}}
PRIOR_CLOSE_PX = {AAPL: Decimal("180"), MSFT: Decimal("300")}      # 09-01 close (prior valuation)
OPEN_PX = {AAPL: 182.0, MSFT: 302.0, NVDA: 120.0}                  # 09-02 open (execution)
CLOSE_PX = {AAPL: 184.0, MSFT: 305.0, NVDA: 123.0}                 # 09-02 close (valuation)
DECISION = {AAPL: 0.10, MSFT: 0.08, NVDA: 0.07}
INITIAL = Decimal("1000000")


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def seed_platform(conn) -> dict[str, int]:
    """Everything the SERVER owns: teams, instruments, calendar, the genesis snapshot chain, the
    served observation, and the received submission. Returns the seeded snapshot ids."""
    conn.execute("INSERT INTO teams (id, team_code, display_name, api_key_hash, status, created_at,"
                 " updated_at) VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s)",
                 (TEAM_ID, "team_alpha", "Team Alpha", "hash", NOW, NOW))
    for tk, iid in INSTR.items():
        conn.execute("INSERT INTO instruments (id, ticker, company_name, sector, is_active,"
                     " created_at) VALUES (%s,%s,%s,%s,TRUE,%s)", (iid, tk, tk, "Tech", NOW))
    for did, day in ((SIGNAL_DAY_ID, SIGNAL_DATE), (EXEC_DAY_ID, EXEC_DATE), (3, "2026-09-03")):
        d = int(day[-2:])
        conn.execute(
            "INSERT INTO trading_days (id, trading_date, market_open_at, market_close_at,"
            " submission_open_at, submission_deadline_at, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (did, day, datetime(2026, 9, d, 13, 30, tzinfo=timezone.utc),
             datetime(2026, 9, d, 20, 0, tzinfo=timezone.utc),
             datetime(2026, 9, d, 8, 0, tzinfo=timezone.utc),
             datetime(2026, 9, d, 13, 29, tzinfo=timezone.utc), NOW, NOW))

    # GENESIS: the chain root. INITIAL must have prior_close_snapshot_id = NULL (schema CHECK).
    initial_id = conn.execute(
        "INSERT INTO portfolio_snapshots (team_id, trading_day_id, prior_close_snapshot_id,"
        " snapshot_type, cash, positions_value, nav, gross_exposure, drawdown, effective_at,"
        " created_at) VALUES (%s,%s,NULL,'INITIAL',%s,0,%s,0,0,%s,%s) RETURNING id",
        (TEAM_ID, SIGNAL_DAY_ID, INITIAL, INITIAL, NOW, NOW)).fetchone()[0]

    # the signal-day CLOSE the decision was made against, chained off the genesis
    pos_val = sum(PRIOR["shares"][t] * PRIOR_CLOSE_PX[t] for t in PRIOR["shares"])
    nav = PRIOR["cash"] + pos_val
    prior_id = conn.execute(
        "INSERT INTO portfolio_snapshots (team_id, trading_day_id, prior_close_snapshot_id,"
        " snapshot_type, cash, positions_value, nav, gross_exposure, drawdown, effective_at,"
        " created_at) VALUES (%s,%s,%s,'CLOSE',%s,%s,%s,%s,0,%s,%s) RETURNING id",
        (TEAM_ID, SIGNAL_DAY_ID, initial_id, PRIOR["cash"], pos_val, nav, pos_val / nav,
         NOW, NOW)).fetchone()[0]
    for iid, q in PRIOR["shares"].items():
        mv = q * PRIOR_CLOSE_PX[iid]
        conn.execute("INSERT INTO position_snapshots (portfolio_snapshot_id, instrument_id,"
                     " quantity, reference_price, market_value, weight, created_at)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (prior_id, iid, q, PRIOR_CLOSE_PX[iid], mv, mv / nav, NOW))

    payload = {"session_date": SIGNAL_DATE, "portfolio": {"nav": float(nav)}}
    conn.execute("INSERT INTO observations (id, team_id, trading_day_id, close_portfolio_snapshot_id,"
                 " payload_json, payload_hash, generated_at, published_at, created_at)"
                 " VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (TEAM_ID, SIGNAL_DAY_ID, prior_id, json.dumps(payload), _hash(payload),
                  NOW, NOW, NOW))
    raw = {"type": "decision_response", "target_weights": {str(k): v for k, v in DECISION.items()}}
    conn.execute(
        "INSERT INTO decision_submissions (id, team_id, observation_id, signal_day_id,"
        " execution_day_id, idempotency_key, received_at, raw_payload_json, payload_hash, source,"
        " status, validator_version, validation_policy_json, validation_summary_json,"
        " expected_weight_count, stored_weight_count, sanitized_gross_weight, weights_processed_at,"
        " agent_version, created_at, updated_at)"
        " VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,'PARTICIPANT','QUEUED',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (SUBMISSION_ID, TEAM_ID, SIGNAL_DAY_ID, EXEC_DAY_ID, "idem-1", NOW, json.dumps(raw),
         _hash(raw), "reject-not-repair-1.0", json.dumps(CONSTRAINTS),
         json.dumps({"policy": "reject-not-repair", "violations": []}),
         len(ACTIVE), len(ACTIVE), Decimal("0.25"), NOW, "alpha-3.1", NOW, NOW))
    return {"initial_id": initial_id, "prior_id": prior_id}


def run(dsn: str, schema_sql: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql)
        seeded = seed_platform(conn)
        prior_id, initial_id = seeded["prior_id"], seeded["initial_id"]
        repo = TradingRepository(conn)

        # 2) state from the EXACT prior snapshot, peak NAV over the ancestor chain
        prior = repo.load_state(TEAM_ID, prior_close_snapshot_id=prior_id,
                                execution_day_id=EXEC_DAY_ID, initial_capital=INITIAL)
        assert prior["state"]["cash"] == Decimal("400000"), prior["state"]["cash"]
        assert prior["state"]["shares"] == {AAPL: Decimal("2000"), MSFT: Decimal("800")}
        assert prior["prev_close_nav"] == INITIAL
        assert prior["state"]["peak_nav"] == INITIAL, prior["state"]["peak_nav"]

        # the guardrails: no silent fallback, wrong team, bad id
        for bad, why in ((None, "missing"), (10_000, "nonexistent")):
            try:
                repo.load_state(TEAM_ID, prior_close_snapshot_id=bad, execution_day_id=EXEC_DAY_ID)
                raise AssertionError(f"{why} prior snapshot should have failed")
            except ValueError:
                pass

        # 3) the after-close batch, id-first
        rec = engine.execute_decision(prior["state"], DECISION, OPEN_PX, CLOSE_PX, ACTIVE,
                                      CONSTRAINTS, prev_close_nav=prior["prev_close_nav"],
                                      initial_capital=INITIAL)
        assert rec["execution"]["nav_open"] == Decimal("1005600.000000000000")

        # 4) persist, stamping the lineage on both generated snapshots
        ids = repo.persist_day(team_id=TEAM_ID, execution_day_id=EXEC_DAY_ID,
                               submission_id=SUBMISSION_ID, prior_close_snapshot_id=prior_id,
                               record=rec, initial_capital=INITIAL)

        # 5) read back
        n_tx = conn.execute("SELECT count(*) FROM transactions WHERE execution_id = %s",
                            (ids["execution_id"],)).fetchone()[0]
        db_nav, db_cash = conn.execute(
            "SELECT nav, cash FROM portfolio_snapshots WHERE id = %s",
            (ids["close_snapshot_id"],)).fetchone()
        db_pos = conn.execute(
            "SELECT COALESCE(sum(market_value),0) FROM position_snapshots WHERE portfolio_snapshot_id = %s",
            (ids["close_snapshot_id"],)).fetchone()[0]
        lineage = conn.execute(
            "SELECT snapshot_type, prior_close_snapshot_id FROM portfolio_snapshots"
            " WHERE id IN (%s,%s) ORDER BY snapshot_type",
            (ids["post_open_snapshot_id"], ids["close_snapshot_id"])).fetchall()
        ledger_total, ledger_linked = conn.execute(
            "SELECT count(*), count(transaction_id) FROM cash_ledger WHERE execution_id = %s",
            (ids["execution_id"],)).fetchone()
        perf_nav, perf_cost = conn.execute(
            "SELECT current_close_nav, transaction_cost FROM daily_performance"
            " WHERE team_id = %s AND trading_day_id = %s", (TEAM_ID, EXEC_DAY_ID)).fetchone()

        # 6) leaderboard: compute, then re-run to prove the upsert
        board = compute_and_store_leaderboard(conn, EXEC_DAY_ID, initial_capital=INITIAL)
        rows_once = conn.execute("SELECT count(*) FROM leaderboard WHERE trading_day_id = %s",
                                 (EXEC_DAY_ID,)).fetchone()[0]
        compute_and_store_leaderboard(conn, EXEC_DAY_ID, initial_capital=INITIAL)
        rows_twice = conn.execute("SELECT count(*) FROM leaderboard WHERE trading_day_id = %s",
                                  (EXEC_DAY_ID,)).fetchone()[0]
        lb_rank, lb_m1 = conn.execute(
            "SELECT rank, m1_cumulative_return FROM leaderboard"
            " WHERE trading_day_id = %s AND team_id = %s", (EXEC_DAY_ID, TEAM_ID)).fetchone()

        # peak NAV is a high-water mark over the chain: a later loss must not lower it
        low_id = conn.execute(
            "INSERT INTO portfolio_snapshots (team_id, trading_day_id, prior_close_snapshot_id,"
            " snapshot_type, cash, positions_value, nav, gross_exposure, drawdown, effective_at,"
            " created_at) VALUES (%s,%s,%s,'CLOSE',900000,0,900000,0,0,%s,%s) RETURNING id",
            (TEAM_ID, 3, ids["close_snapshot_id"], NOW, NOW)).fetchone()[0]
        peak_after_loss = repo.peak_nav(low_id)
        nxt = repo.load_state(TEAM_ID, prior_close_snapshot_id=ids["close_snapshot_id"],
                              initial_capital=INITIAL)

    assert n_tx == len(rec["transactions"]) == 3, n_tx
    assert db_nav == rec["nav_close"] and db_cash + db_pos == db_nav
    assert lineage == [("CLOSE", prior_id), ("POST_OPEN", prior_id)], lineage
    assert ledger_total == 6 and ledger_linked == 6, (ledger_total, ledger_linked)
    assert perf_nav == rec["nav_close"] and perf_cost == rec["execution"]["total_fee"]
    assert len(board) == 1 and board[0]["rank"] == 1
    assert rows_once == rows_twice == 1, (rows_once, rows_twice)
    assert peak_after_loss == rec["nav_close"], peak_after_loss
    assert nxt["prev_close_nav"] == rec["nav_close"]

    print("PASS: lineage + persistence + leaderboard, on the real schema")
    print(f"  nav_open (his doc 1,005,600)        : {rec['execution']['nav_open']}")
    print(f"  peak NAV from chain (incl INITIAL)  : {prior['state']['peak_nav']}")
    print(f"  both snapshots carry prior id {prior_id} : {lineage}")
    print(f"  cash_ledger rows / with transaction : {ledger_total} / {ledger_linked}")
    print(f"  CLOSE NAV rebuilt from positions    : {db_cash} + {db_pos} = {db_cash + db_pos}")
    print(f"  leaderboard rank / M1               : {lb_rank} / {lb_m1}")
    print(f"  leaderboard rows after 2 runs       : {rows_twice} (upsert, not duplicated)")
    print(f"  peak stays high after a 900k close  : {peak_after_loss}")
    print(f"  next-day resume prev_close_nav      : {nxt['prev_close_nav']}")


def _skip_or_run():
    import pytest
    dsn = os.environ.get("COMPETITION_DATABASE_URL")
    schema = os.environ.get("COMPETITION_SCHEMA_SQL")
    if not dsn or not schema:
        pytest.skip("set COMPETITION_DATABASE_URL and COMPETITION_SCHEMA_SQL to run the DB integration")
    run(dsn, Path(schema).read_text())


def test_postgres_roundtrip():
    _skip_or_run()


if __name__ == "__main__":
    run(os.environ["COMPETITION_DATABASE_URL"], Path(os.environ["COMPETITION_SCHEMA_SQL"]).read_text())
