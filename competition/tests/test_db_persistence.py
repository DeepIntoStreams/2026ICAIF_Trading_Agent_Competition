"""The daily batch emits a COMPLETE, reconciling, persistable record (DB-compatibility).

These lock down the seam the live deployment relies on: `execute_decision` returns every field
his schema needs, the money adds up (in Decimal), the score is unchanged by the enrichment,
`validate_decision` + `pre_validated` reproduce the inline path, and `db_adapter` never emits a
column outside the schema contract.
"""

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT.parent / "src"))

import db_adapter
import engine

TICKERS = ["AAA", "BBB", "CCC"]
CONSTRAINTS = {"max_asset_weight": 0.50, "max_gross_exposure": 1.00}
OPEN = {"AAA": 100.0, "BBB": 50.0, "CCC": 25.0}
CLOSE = {"AAA": 110.0, "BBB": 48.0, "CCC": 26.0}
START = {"cash": 1_000_000.0, "shares": {}, "peak_nav": 1_000_000.0}


def _record(weights, state=START, submitted=True, prev_nav=1_000_000.0, pre_validated=False):
    return engine.execute_decision(state, weights, OPEN, CLOSE, TICKERS, CONSTRAINTS,
                                   submitted=submitted, prev_close_nav=prev_nav,
                                   pre_validated=pre_validated)


def _f(x):
    return float(x)


def test_record_has_every_block_and_uses_decimal():
    r = _record({"AAA": 0.4, "BBB": 0.3})
    for key in ("decision", "transactions", "execution", "open_snapshot",
                "close_snapshot", "performance", "new_state", "nav_close"):
        assert key in r, f"missing {key}"
    assert isinstance(r["nav_close"], Decimal)                 # money is Decimal, not float
    assert isinstance(r["execution"]["total_fee"], Decimal)
    tx = r["transactions"]
    assert {t["instrument"] for t in tx} == {"AAA", "BBB"}          # only the two bought
    for t in tx:
        assert set(t) >= {"instrument", "side", "shares_before", "target_shares", "quantity",
                          "price", "gross_amount", "fee", "cash_change"}
        assert t["side"] == "BUY" and t["quantity"] > 0
        assert isinstance(t["cash_change"], Decimal)


def test_decimal_is_exact_no_float_noise():
    """40% of 1,000,000 into AAA @ 100 buys exactly 4,000 shares for exactly 400,000 gross."""
    r = _record({"AAA": 0.4})
    aaa = next(t for t in r["transactions"] if t["instrument"] == "AAA")
    assert aaa["gross_amount"] == Decimal("400000.000000000000")   # not 399999.9999999994
    assert aaa["quantity"] == Decimal("4000.000000000000")


def test_cash_and_fees_reconcile():
    r = _record({"AAA": 0.4, "BBB": 0.3, "CCC": 0.2})
    e = r["execution"]
    assert abs(_f(sum(t["cash_change"] for t in r["transactions"]))
               - _f(e["cash_after"] - e["cash_before"])) < 1e-6
    assert abs(_f(sum(t["fee"] for t in r["transactions"])) - _f(e["total_fee"])) < 1e-6
    assert abs(_f(e["total_fee"]) - _f(r["transaction"]["traded_notional"]) * float(engine.FEE_RATE)) < 1e-6
    assert abs(_f(r["open_snapshot"]["nav"]) - _f(e["cash_after"] + r["open_snapshot"]["positions_value"])) < 1e-6
    assert r["close_snapshot"]["nav"] == r["nav_close"]


def test_enrichment_does_not_change_the_numbers():
    """The summary keys the metrics depend on match a plain settle_open + mark_to_close."""
    w = {"AAA": 0.4, "BBB": 0.3, "CCC": 0.2}
    o = engine.settle_open(START, w, OPEN, TICKERS, CONSTRAINTS)
    c = engine.mark_to_close(o["new_state"], CLOSE)
    r = _record(w)
    assert r["nav_close"] == c["nav_close"]
    assert r["transaction"] == o["transaction"]
    assert r["new_state"] == c["new_state"]


def test_validate_execute_split_matches_inline():
    """validate at receipt + execute(pre_validated) == validate-inside-execute."""
    w = {"AAA": 0.4, "BBB": 0.3}
    v = engine.validate_decision(w, TICKERS, CONSTRAINTS)
    assert v["ok"] and v["accepted"] is not None and not v["violations"]
    r_split = engine.execute_decision(START, v["accepted"], OPEN, CLOSE, TICKERS, CONSTRAINTS,
                                      pre_validated=True, prev_close_nav=1_000_000.0)
    r_inline = _record(w)
    assert r_split["nav_close"] == r_inline["nav_close"]
    assert r_split["new_state"] == r_inline["new_state"]
    assert r_split["executed"] is True


def test_validate_decision_rejects_over_cap():
    v = engine.validate_decision({"AAA": 0.9}, TICKERS, CONSTRAINTS)   # 0.9 > 0.50 cap
    assert not v["ok"] and v["accepted"] is None and v["violations"]


def test_reject_not_repair_leaves_no_trade_and_no_weight_rows():
    r = _record({"AAA": 0.9})                                   # rejected inline
    assert r["executed"] is False and r["violations"]
    assert r["transactions"] == []
    assert r["new_state"]["shares"] == {}                       # holdings unchanged (still flat)
    rows = db_adapter.day_rows(r, team_code="t1", signal_date="2026-06-04",
                               execution_date="2026-06-05", active_instruments=TICKERS,
                               idempotency_key="k1", received_at="2026-06-05T09:00:00Z")
    assert rows["decision_submissions"][0]["status"] == "REJECTED"
    assert rows["submission_weights"] == []                     # no weights stored for a reject
    assert "executions" not in rows and "transactions" not in rows


def test_adapter_rows_conform_to_schema_contract():
    r = _record({"AAA": 0.4, "BBB": 0.3, "CCC": 0.2})
    rows = db_adapter.day_rows(r, team_code="t1", signal_date="2026-06-04",
                               execution_date="2026-06-05", active_instruments=TICKERS,
                               idempotency_key="k1", received_at="2026-06-05T09:00:00Z")
    for table, table_rows in rows.items():
        assert table in db_adapter.SCHEMA_COLUMNS, f"unknown table {table}"
        allowed = db_adapter.SCHEMA_COLUMNS[table]
        for row in table_rows:
            extra = set(row) - allowed
            assert not extra, f"{table} emits columns outside schema: {extra}"
    assert {s["snapshot_type"] for s in rows["portfolio_snapshots"]} == {"POST_OPEN", "CLOSE"}
    assert len(rows["executions"]) == 1 and len(rows["transactions"]) == 3


def test_cash_ledger_chains_and_reconciles():
    r = _record({"AAA": 0.4, "BBB": 0.3, "CCC": 0.2})
    rows = db_adapter.day_rows(r, team_code="t1", signal_date="2026-06-04",
                               execution_date="2026-06-05", active_instruments=TICKERS,
                               idempotency_key="k1", received_at="2026-06-05T09:00:00Z")
    ledger = rows["cash_ledger"]
    assert ledger, "expected cash-ledger events for an executed day"
    for a, b in zip(ledger, ledger[1:]):                        # balances chain
        assert a["balance_after"] == b["balance_before"]
    assert ledger[0]["balance_before"] == r["execution"]["cash_before"]
    assert ledger[-1]["balance_after"] == r["execution"]["cash_after"]   # ends at cash_after
    assert {e["event_type"] for e in ledger} == {"TRADE", "FEE"}


def test_reject_day_has_no_cash_ledger():
    rows = db_adapter.day_rows(_record({"AAA": 0.9}), team_code="t1", signal_date="2026-06-04",
                               execution_date="2026-06-05", active_instruments=TICKERS,
                               idempotency_key="k1", received_at="2026-06-05T09:00:00Z")
    assert "cash_ledger" not in rows                            # nothing moved -> no ledger rows


def test_advance_day_builds_next_observation_from_finalized_state():
    panel = {"session_date": "2026-06-05", "assets": [{"ticker": t} for t in TICKERS],
             "constraints": CONSTRAINTS}
    out = engine.advance_day(START, {"AAA": 0.4, "BBB": 0.3}, OPEN, CLOSE, TICKERS, CONSTRAINTS,
                             observation_panel=panel, prev_close_nav=1_000_000.0)
    rec = out["record"]
    assert rec["nav_close"] == rec["close_snapshot"]["nav"]
    obs = out["next_observation"]
    assert obs is not None and "portfolio" in obs
    assert set(obs["portfolio"]["weights"]) == {"AAA", "BBB"}   # reflects the just-closed holdings
    assert abs(obs["portfolio"]["nav"] - _f(rec["nav_close"])) < 1e-6
    # no panel -> no observation (final day)
    assert engine.advance_day(START, {"AAA": 0.4}, OPEN, CLOSE, TICKERS, CONSTRAINTS,
                              prev_close_nav=1_000_000.0)["next_observation"] is None


def test_multi_day_state_carries_and_metrics_run():
    """Drive two days one batch at a time, threading state -> the live loop, and score it."""
    state = dict(START)
    prev_nav = engine.INITIAL_CAPITAL
    navs = []
    for w in [{"AAA": 0.5, "BBB": 0.3}, {"AAA": 0.3, "CCC": 0.4}]:
        r = engine.execute_decision(state, w, OPEN, CLOSE, TICKERS, CONSTRAINTS,
                                    prev_close_nav=prev_nav)
        state = r["new_state"]                                  # persist + resume next day
        prev_nav = r["nav_close"]
        navs.append(r["nav_close"])
        assert r["performance"]["current_close_nav"] == r["nav_close"]
    assert len(navs) == 2 and all(n > 0 for n in navs)
