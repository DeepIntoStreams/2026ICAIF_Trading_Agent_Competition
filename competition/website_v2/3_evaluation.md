# Evaluation

## Evaluation Overview

Throughout the live phase, we evaluate each agent's daily decisions across **four dimensions** to
ensure a comprehensive assessment: **profitability, risk-adjusted performance, risk management,
and execution quality**. A strong agent is not just about profitable - it also earns its returns without
taking wild risks, trades with discipline, and stays within the rules.

- **Profitability** - did the agent actually make money? (M1, M2)
- **Risk-adjusted performance** - was the return worth the risk taken to get it? (M3, M4)
- **Risk management** - how bad were the losses along the way? (M5, M6, M7)
- **Execution quality** - was the agent disciplined and rule-abiding? (M8, M9)

Teams are **ranked separately under each of the nine metrics**, and those nine ranks are
**averaged** into a single overall rank. A **lower average rank is better**. This rewards agents
that are well-rounded - genuinely profitable, steady under stress, and disciplined - rather than
those that win on any single number.

The trading rules that govern the portfolio (starting capital, position limits, transaction
fees, and next-open execution) are defined once in the competition's trading rules; see the
**Overview** and **Terms** pages. Here we use only the portfolio-return convention needed to read
the metrics.

## From the portfolio to daily returns

Let NAV_t be the portfolio's value at the close of live trading day t (t = 0, 1, ..., T), where
NAV_0 is its value at the start of the live period. Each day's return is simply how much the
portfolio grew or shrank:

  r_t = NAV_t / NAV_(t-1) - 1,   for t = 1, ..., T.

NAV is valued each day under the official execution and cost rules, and every metric below is
built from this NAV series and its daily returns.

## The metrics (M1-M9)

Each metric is marked higher-is-better (↑) or lower-is-better (↓). The three risk metrics
(M5-M7) are reported as **positive loss numbers**, so 0 means "no loss" and a bigger number
means "worse."

**Profitability**

- **M1 - Cumulative Return** (↑) - the bottom line: how much the portfolio grew over the whole
  live period. M1 = NAV_T / NAV_0 - 1.
- **M2 - Daily Win Rate** (↑) - how often the agent has a good day, i.e. the share of trading
  days that end in profit. M2 = (number of days with r_t > 0) / T. A flat day (r_t = 0) does not
  count as a win.

**Risk-adjusted performance** (annualized with a factor of 252; risk-free rate 0)

- **M3 - Sharpe Ratio** (↑) - return earned per unit of overall volatility, the classic measure
  of risk-adjusted performance. M3 = sqrt(252) * mean(r) / s, where s is the **sample** standard
  deviation of the returns (ddof = 1). If the returns never vary (s = 0, e.g. a fully idle
  portfolio), the ratio is undefined and ranks **last** on M3.
- **M4 - Sortino Ratio** (↑) - like Sharpe, but it only counts *downside* volatility, so an agent
  is not punished for big up-moves. M4 = sqrt(252) * mean(r) / d, where the downside deviation is
  d = sqrt( mean( min(r_t, 0)^2 ) ) with a target return of 0. If the agent never has a losing
  day, M4 is undefined and ranks **last** on M4.

**Risk management** (positive loss numbers; lower is better)

- **M5 - Maximum Drawdown** (↓) - the worst peak-to-trough fall the portfolio suffers, a direct
  measure of how painful the ride is. M5 = max_t ( (peak_t - NAV_t) / peak_t ), where peak_t is
  the highest NAV up to day t.
- **M6 - Value at Risk, 95%** (↓) - how bad an ordinary bad day looks: the loss the agent should
  exceed only about 5% of the time. With Q the 5th-percentile daily return, M6 = max(0, -Q).
- **M7 - Expected Shortfall, 95%** (↓) - how bad the *worst* days actually are on average, going
  past VaR into the tail. M7 = mean( -r_t : r_t <= Q ), reported as a positive loss. If no day
  reaches the tail, M7 is undefined and ranks **last** on M7.

**Execution quality**

- **M8 - Turnover** (↓) - how much trading the agent does; heavy churn signals instability and
  piles up cost. M8 = (total traded notional over the live period) / average NAV.
- **M9 - Violation Rate** (↓) - how disciplined the agent is about the rules: the share of
  trading days on which it submitted at least one invalid decision. M9 = (number of days with
  >= 1 violation) / T. Several violations on the same day count once.

## What happens when a decision is invalid

If a decision breaks a trading rule, it **fails and is not executed** - we do **not** quietly
repair, clip, or rescale it. On a failed decision the portfolio simply **keeps the previous
trading day's holdings**, still valued under the official rules, and that day counts as **one
violation day** toward M9. A decision is invalid if, for instance, it exceeds the single-asset
cap, has weights summing above 1, includes negative weights or unknown tickers, or is malformed
or missing. The exact field checks and numerical tolerance live in the **Starter Kit** and the
submission system.

## How the ranking works

1. For each metric M1-M9, rank every team from best to worst (best = rank 1), following that
   metric's direction. **Teams with the same value share the same average rank.**
2. A team's overall score is the **average of its nine metric ranks**; the **lowest average rank
   wins**.
3. If two teams tie overall, we break it by higher cumulative return (M1), then lower maximum
   drawdown (M5), then earlier team registration.

Metric ranks are computed for **all** teams first, and eligibility is checked afterwards. A team
that does not submit the required final materials, or whose results the organizers cannot
reproduce, **does not receive a final ranking** - but removing it **does not** change the ranks
of the remaining teams. The **top two teams** in the final valid ranking are invited to present
at ICAIF 2026.

## Validation vs. the live competition

- **Validation** results are **unofficial**. They only let you confirm that your agent connects
  to and runs on our evaluation system, and they **do not count toward the final ranking**.
- The **official ranking** comes only from your agent's **live** daily decisions (M1-M9).
- Live standings are **provisional**; the ranking becomes **final** only after the live phase
  ends and the final-material and reproducibility checks are done.

## Reproducibility

Reproducibility is a **final-ranking eligibility requirement, not a scored metric**. To stay
eligible, top teams submit the required final materials and the organizers reproduce their
results; the details are on the **Data & Submission** and **Terms** pages. Nothing about
reproducibility feeds into the metric ranking itself.

## Evaluation frequency

The live leaderboard refreshes **once per trading day**, after each day's decisions are executed.
Validation results come back whenever you validate.
