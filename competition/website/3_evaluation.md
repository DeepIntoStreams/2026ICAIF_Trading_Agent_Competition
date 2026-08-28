# Evaluation

## Evaluation Overview

Each agent manages a live long-only portfolio, and its decisions are evaluated across five
dimensions to ensure a comprehensive assessment: **profitability, risk-adjusted performance,
risk management, execution quality, and integrity (reproducibility).** Final rankings are
determined by a **weighted average of an agent's ranks** across all metrics - with integrity
weighted most heavily - followed by a two-stage winner-selection procedure (below). This
ensures winning agents combine return, robust risk control, disciplined execution, and code
that faithfully reproduces the decisions they submitted live.

## Trading rules

- **Capital & constraints.** Each agent starts with **$1,000,000**, long-only, **≤ 10%** per
  asset, **gross exposure ≤ 100%**; the remainder is cash.
- **Execution & cost.** Target weights submitted by the **close of day t** are executed at the
  **open of day t+1**, with a **0.1%** fee on both buys and sells. Fractional shares are
  allowed. Next-open execution prevents look-ahead.
- **Action validation.** Invalid actions are repaired before execution - negative or oversized
  weights are clipped, excessive gross exposure is scaled down; if an agent fails to respond,
  its previous weights are retained. Every repair is recorded and counts toward the violation
  rate (M9).

## Evaluation metrics

Let the daily portfolio return be `r_t = NAV_t / NAV_{t-1} − 1` over the evaluation horizon.

**Profitability**
- **M1 - Cumulative Return** (↑): total portfolio growth, `NAV_end / NAV_start − 1`.
- **M2 - Daily Win Rate** (↑): fraction of days with `r_t > 0`.

**Risk-adjusted performance**
- **M3 - Sharpe Ratio** (↑): `mean(r) / std(r)`, annualized.
- **M4 - Sortino Ratio** (↑): return per unit of downside deviation, annualized.

**Risk management**
- **M5 - Maximum Drawdown** (↓): worst peak-to-trough decline of the equity curve.
- **M6 - Value at Risk, 95%** (↓): the 5% tail-loss quantile of daily returns.
- **M7 - Expected Shortfall, 95%** (↓): mean loss beyond the VaR threshold.

**Execution quality**
- **M8 - Turnover** (↓): total traded notional relative to portfolio value.
- **M9 - Violation Rate** (↓): fraction of decision days whose action required repair (or the
  agent failed to respond).

**Integrity (weighted highest)**
- **M10 - Reproducibility Deviation** (↓): the average distance between the weights a team
  **submitted live** and the weights its **frozen code re-produces** when the organizers re-run
  it on the recorded observations - `mean_t Σ_i |w_{i,t} − ŵ_{i,t}|`. A deterministic, honest
  agent reproduces its live weights exactly (deviation 0). Because faithful reproduction is
  essential to a live competition's integrity, **M10 carries a higher coefficient in the
  ranking** - **default weight ×2 vs ×1 for M1-M9 (tunable by the organizers).**

Arrows show the preferred direction (↑ higher is better, ↓ lower is better).

## Ranking and winner selection

Each metric is transformed so that **lower is better** (return-type metrics M1-M4 are
inverted). Agents are ranked on each metric, and the score is the **weighted average of an
agent's ranks across all metrics**, with **integrity (M10) weighted more heavily** than the
others (default ×2). A lower weighted-average rank is better.

The winner is chosen in **two stages, with an eligibility loop**:

1. **Shortlist.** Rank all teams by weighted-average rank and take the **top 10**.
2. **Audit.** Each shortlisted team is audited for **look-ahead bias and other unallowed
   actions** - using same-day-or-future prices, news timestamped after the cutoff, forbidden
   news sources, non-allowed models, or code that does not reproduce the submitted weights
   within tolerance. Any team in violation is **disqualified**.
3. **Winner.** The winner is the **highest-ranked team in the shortlist that passes the audit.**
4. **Loop.** If **every** team in the shortlist is disqualified, the shortlist is refilled with
   the **next 10** teams and steps 2-3 repeat, until a valid winner is found. Remaining valid
   teams fill the final standings in weighted-rank order.

Ties are broken by higher cumulative return, then lower maximum drawdown.

## Reproducibility and the audit

Given the same observations and frozen code, an agent must return the same weights. Two things
follow from this:

- **Scored (M10).** The *degree* of reproduction - how far the re-run weights deviate from the
  submitted ones - is a ranked metric (above), rewarding deterministic, faithful agents.
- **Pass/fail (audit).** Beyond a tolerance, or where the code shows look-ahead or other
  unallowed behaviour, the submission is **disqualified** in the winner-selection loop.

You can verify your own agent locally with the Starter Kit's look-ahead audit and by re-running
it on the recorded observations before submitting.

## Evaluation frequency

- **Development phase:** the **validation environment** scores your agent on the held-out 2025
  period on submission (up to the per-day submission limit in **Terms**). Validate offline with
  the Starter Kit first.
- **Live phase:** the leaderboard updates **once per trading day**, after each day's decisions
  are executed at the next market open. All metrics are computed on the same engine for both the
  public leaderboard and the final ranking.
