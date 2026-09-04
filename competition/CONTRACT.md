# Trading core - data contract

## The daily call (server side, once after the T+1 close)

```python
from engine import execute_decision            # or validate_decision at receipt, see below
from trading_repo import TradingRepository

repo  = TradingRepository(conn)                 # a psycopg3 connection
prior = repo.load_state(team_id)                # reconstruct state from the last CLOSE snapshot
rec   = execute_decision(
            prior["state"],                     # portfolio to resume from
            accepted_weights,                   # the stored, validated target vector (or None to hold)
            open_prices, close_prices,          # the T+1 bar
            active_instrument_ids, constraints,
            prev_close_nav=prior["prev_close_nav"],
            pre_validated=True)                 # weights already validated at receipt
repo.persist_day(team_id=team_id, execution_day_id=day_id,
                 submission_id=sub_id, record=rec)   # one atomic transaction
```

Validation is a separate receipt-time step (your `weight_validator`):
`validate_decision(raw_weights, active_instrument_ids, constraints) -> {accepted, violations, ok}`
(reject-not-repair: invalid = whole decision rejected, portfolio held, one M9 violation).

## A. Inputs

All money / quantity / price / weight values are `Decimal`. Keys are `instrument_id` (int) -
the core is id-first and key-agnostic (ticker strings also work, but ids are preferred).

| Input | Shape | Meaning |
| --- | --- | --- |
| `state` | `{"cash": Decimal, "shares": {instrument_id: Decimal}, "peak_nav": Decimal}` | portfolio to resume from (from `repo.load_state`) |
| `target_weights` | `{instrument_id: float\|Decimal}` or `None` | the accepted vector; omitted names = 0; `None` = hold (no trade) |
| `open_prices` | `{instrument_id: Decimal}` | execution-day adjusted open (fill price) |
| `close_prices` | `{instrument_id: Decimal}` | execution-day adjusted close (valuation price) |
| `instruments` | `[instrument_id, ...]` | the active universe for the day (validation checks against this) |
| `constraints` | `{"max_asset_weight": float, "max_gross_exposure": float, ...}` | per-asset cap, gross cap |
| `prev_close_nav` | `Decimal \| None` | previous CLOSE NAV, for the daily return (None on day 1) |

## B. The record returned by `execute_decision`

One dict, one block per trading table. Top-level keys:

| Key | Shape | Persists to |
| --- | --- | --- |
| `new_state` | `{cash, shares, peak_nav}` | (carried forward; also reconstructable from the CLOSE snapshot) |
| `decision` | `{raw, accepted, submitted, executed, violations}` | your `decision_submissions` / `submission_weights` |
| `execution` | `{nav_open, cash_before, cash_after, total_buy_value, total_sell_value, total_fee}` | `executions` |
| `transactions` | list of `{instrument, side, shares_before, target_shares, quantity, price, gross_amount, fee, cash_change}` | `transactions` |
| `open_snapshot` | `{cash, positions_value, nav, gross_exposure, positions:[{instrument, quantity, reference_price, market_value, weight}]}` | `portfolio_snapshots` (POST_OPEN) + `position_snapshots` |
| `close_snapshot` | same shape + `drawdown` | `portfolio_snapshots` (CLOSE) + `position_snapshots` |
| `performance` | `{previous_close_nav, current_close_nav, daily_return, cumulative_return, turnover, transaction_cost, drawdown}` | `daily_performance` |
| `nav_close`, `drawdown`, `transaction`, `weights`, `violations`, `executed` | summary scalars | (metrics / convenience) |

`cash_ledger` rows (TRADE + FEE legs, running balance) are derived by
`db_adapter.cash_ledger_rows(record, ...)` and written by the repo.

## C. Worked example - his §6-§12, one day (2026-09-01 signal -> 2026-09-02 execution)

**State resumed** (`repo.load_state`, from the 2026-09-01 CLOSE):
```
cash 400000 ; shares {1: 2000, 2: 800} ; peak_nav 1000000     # ids: 1=AAPL 2=MSFT 3=NVDA 4=JPM
```
**Inputs to `execute_decision`:**
```
target_weights {1: 0.10, 2: 0.08, 3: 0.07}          # accepted (all under the 0.30 cap -> valid)
open_prices    {1: 182, 2: 302, 3: 120}             # 09-02 open
close_prices   {1: 184, 2: 305, 3: 123}             # 09-02 close
instruments    [1, 2, 3, 4] ; constraints {max_asset_weight: 0.30, max_gross_exposure: 1.0}
prev_close_nav 1000000
```
**Record returned (key numbers):**
```
execution   nav_open 1005600.000000000000   (= 400000 + 2000*182 + 800*302)
            cash_before 400000  cash_after 753705.016000000000
            total_buy 70392  total_sell 424592  total_fee 494.984000000000
transactions
  {instrument 1, SELL, shares_before 2000, target_shares 552.527472527473, qty 1447.472527472527,
   price 182, gross 263440, fee 263.440, cash_change +263176.560}
  {instrument 2, SELL, shares_before 800,  target_shares 266.384105960265, qty 533.615894039735,
   price 302, gross 161152, fee 161.152, cash_change +160990.848}
  {instrument 3, BUY,  shares_before 0,    target_shares 586.600000000000, qty 586.6,
   price 120, gross 70392,  fee 70.392,  cash_change -70462.392}
close_snapshot  cash 753705.016  positions_value 255064.007262935857  nav 1008769.023262935857
                positions: {1: qty 552.527..., mv 101665.054..., w 0.1008}, {2: ...}, {3: ...}
performance     previous_close_nav 1000000  current_close_nav 1008769.023262935857
                daily_return 0.008769023263  cumulative_return 0.008769023263
                turnover 0.492227525855  transaction_cost 494.984  drawdown 0
```
**Persisted** by `repo.persist_day(...)`: 1 `executions` row, 3 `transactions`, 6 `cash_ledger`
legs (3 TRADE + 3 FEE, balance ends at 753705.016 = cash_after), 2 `portfolio_snapshots`
(POST_OPEN + CLOSE) with 3 `position_snapshots` each, 1 `daily_performance`. Reconstructable:
`SELECT cash + sum(market_value)` on the CLOSE snapshot == `nav_close` == 1008769.023262935857.
The next `repo.load_state` returns cash 753705.016 and prev_close_nav 1008769.023 - the loop closes.

## D. `TradingRepository` API

```python
repo = TradingRepository(conn)                     # psycopg3 Connection

repo.load_state(team_id, initial_capital=Decimal("1000000"))
  -> {"state": {cash, shares:{instrument_id: qty}, peak_nav},
      "prev_close_nav": Decimal | None, "prior_snapshot_id": int | None}
  # rebuilds from the latest CLOSE snapshot; peak_nav = max historical CLOSE nav; day-1 = initial state

repo.persist_day(team_id, execution_day_id, submission_id, record,
                 initial_capital=Decimal("1000000")) -> {execution_id, post_open_snapshot_id, close_snapshot_id}
  # one atomic transaction; works for a fill OR a held/rejected day (0 trades, empty cash_ledger)
```

## Notes
- **Decimal everywhere** -> exact `NUMERIC(28,12)`; no float drift.
- **Held / rejected day:** call `execute_decision` with `submitted=False` (no decision) or a rejected
  vector; the record has an execution with zero trades, empty `transactions`/`cash_ledger`, and the
  portfolio is carried unchanged and valued at the close. Persist it the same way (with a FALLBACK
  submission on your side).
- **`peak_nav`** is not a column - the repo derives it as the max historical CLOSE NAV, so drawdown
  survives a restart with no extra state.
- **Verified:** `competition/tests/integration_postgres.py` runs this whole flow against the real
  `schema.sql` on `postgres:16` and checks every reconstruction.
