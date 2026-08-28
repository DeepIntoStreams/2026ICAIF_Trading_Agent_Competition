# ACM ICAIF 2026 Trading Agent Competition - Official Rules

Version 0.1 (draft). This document turns the high-level proposal
(`2026_ICAIF_trading_agent_competition.pdf`) into concrete, operational rules: exactly what
data participants receive, the standardized code interface they submit, the daily protocol,
the trading and validation rules, how scores are computed, and how results are audited and
reproduced. Where a rule is still an organizer decision it is marked **[TBC]**.

---

## 1. Overview

Each team builds a **trading agent** that manages a long-only portfolio over a fixed universe
of **30 U.S.-listed equities**. On each trading day the agent receives the day's market data,
company fundamentals, and a news stream, plus its own portfolio state, and returns **target
portfolio weights** for the next trading day. Agents compete in one common, live environment
on newly released market data. Rankings reward not just profit but risk management and
disciplined execution (Section 6).

There is no restriction on how an agent is built (rules, reinforcement learning, forecasting,
LLMs, or any combination), provided it implements the standardized interface (Section 4) and
obeys the fairness rules (Section 8).

**Participation model.** Teams develop and backtest their strategy **on their own machines**
using the provided framework (`competition/code`) and data (`competition/data`), writing their
strategy in `competition/alpha`. On each live trading day they run their agent locally and
**submit only the resulting target weights** to the competition website.

---

## 2. Task and Universe

- Universe: 30 U.S. equities across 6 sector groups (technology, finance, healthcare,
  consumer, industrial & energy, communication & utilities). The fixed ticker list is
  published in `data/universe.json`.
- On trading day `k`=1,t the agent maps `(observation_k, portfolio_state_k) -> portfolio_stock_weights`,
  executed on day `t+1`. The task is sequential: each action changes the portfolio through
  execution and cost, and returns compound over the evaluation horizon.

---

## 3. Repository layout and data

The platform has **three top-level directories**:

| Directory | Owner | Contents |
| --- | --- | --- |
| `competition/data/` | organizer | the official price + news data, published daily (Section 3.1) |
| `competition/code/`  | organizer | the framework given to everyone: the SDK (agent API), the local backtester, the panel builder, the execution/scoring engine |
| `competition/alpha/` | **participant** | the team's strategy - the ONE thing they write and submit (Section 4) |

Participants also keep any self-collected public news under `competition/alpha/proprietary_news/`.

### 3.1 Official data (organizer-published, identical for all teams)

Published to `competition/data/` each trading day:

| Path | Content |
| --- | --- |
| `data/market/daily/<date>.json` | Official adjusted OHLCV for the 30 tickers for that day. |
| `data/observations/<date>.json` | The day's shared observation panel (see 3.3): market features, fundamental features, and the official news stream. Identical for every team. |
| `data/universe.json` | The fixed ticker list with company name and sector. |

The **official competition** runs on market data released live during
the competition window; agents are scored on newly released data, not a fixed backtest.

### 3.2 Participant data: official + proprietary news

- The official observation already contains an organizer-provided news stream.
- Teams **may additionally retrieve their own news from public sources** and place it under
  `code/proprietary_news/`. Only **public / open / free** sources are allowed. Private, paid,
  or proprietary data feeds are **forbidden** unless explicitly permitted **[TBC]**.
- Any external news a team uses must (a) be timestamped and predate the decision cutoff (no
  look-ahead, Section 8) and (b) be declarable via `declared_news_sources()` for the audit.

### 3.3 The observation (what the agent sees each day)

A single JSON object validated against `schemas/observation_with_news.schema.json`:

| Field | Meaning |
| --- | --- |
| `session_date` | The trading day. |
| `event_time_utc` | The decision cutoff. Every news item's `available_at_utc` is <= this. |
| `assets` | The 30 tradable stocks: `ticker, company_name, sector, open_price`. |
| `market_features` | Per ticker: rolling returns, momentum, volatility, trend, volume. |
| `fundamental_features` | Per ticker: profitability, growth, leverage ratios (point-in-time). |
| `portfolio` | The team's current `weights`, `cash_ratio`, `nav`. Team-specific. |
| `constraints` | `long_only, max_asset_weight, max_gross_exposure, fee_rate`. |
| `news` | Official news items available at or before the cutoff. |

---

## 4. The submission: `competition/alpha/` (the team's strategy)

A submission is a self-contained code bundle. The **alpha is the only thing a team writes.**

```
alpha/
  agent.py              # REQUIRED: defines class Agent(BaseAgent) with decide(observation)
  requirements.txt      # pinned dependencies (frozen for reproducibility)
  proprietary_news/     # OPTIONAL: the team's own public-source news store
  MANIFEST.json         # team_id, entrypoint, agent_version, python version
```

On live days the team runs this locally and submits the resulting weights (Section 10.1); the
code itself is provided to the organizer only if the team places in the top ranks, for the
reproducibility audit (Section 7).

### 4.1 The standardized API (input / output)

Every submission subclasses `BaseAgent` (in `code/agent_base.py`) and provides a class named
`Agent`:

```python
class Agent(BaseAgent):
    agent_version = "my-alpha-1.0"
    def setup(self, universe, constraints): ...          # called once
    def decide(self, observation) -> dict[str, float]:   # each day -> {ticker: weight}
```

- **Input**: one `observation` dict (Section 3.3).
- **Output**: `target_weights` mapping ticker -> weight. Unallocated capital stays in cash.
  Validated against `schemas/decision_response.schema.json`.
- This interface is the entire contract. Teams may use any internal design.

### 4.2 Reproducibility (mandatory)

Given the same observation and the same frozen code, `decide` **must** return the same
weights. Seed any randomness in `setup`. The organizer re-runs top teams' agents on the
recorded observations and checks the weights match (Section 7); a submission that does not
reproduce is **disqualified**. Determinism is verified by the reproducibility audit, which
re-executes `code/` against the logged observations.

### 4.3 Develop and backtest locally (self-service)

The framework in `competition/code/` lets a team run the exact official evaluation on the
historical data on their own machine, before ever submitting:

```
python competition/code/backtest.py --alpha competition/alpha \
    --data-root <data> --start <YYYY-MM-DD> --end <YYYY-MM-DD> [--check-lookahead]
```

It runs your alpha with the official rules (next-open execution, 0.1% fee, caps) and prints
M1-M9 - the same engine that scores the leaderboard, so your local number matches the official
one. Two look-ahead protections are built in:

1. **Guarantee (always on).** Every daily observation is verified point-in-time: it contains
   nothing dated after the decision cutoff. If your alpha uses only the observation, it cannot
   leak the future.
2. **Audit (`--check-lookahead`).** For several decision days the backtester removes all data
   after that day and re-checks your decision. A leak-free decision depends only on data up to
   that day and must not change; if it changes, your alpha read future data and the audit
   reports **look-ahead bias** (see `competition/examples/lookahead_cheater` for a caught case).

### 4.4 Reproducibility and time budget

- **Determinism (mandatory).** Given the same observation, `decide` must return the same
  weights; seed any randomness in `setup`. The reproducibility audit (Section 7) re-runs top
  teams' `alpha/` on the logged observations and requires the posted weights to match.
- **Time budget.** Your daily `decide` must finish within the per-day limit of **60 seconds**
  **[TBC]** on your own machine; a failed or late submission triggers the retain-previous-weights
  policy (Section 5.4).

---

## 5. Trading Rules and Execution

### 5.1 Portfolio constraints

- Starting capital: **$1,000,000** cash.
- **Long-only**; maximum **10%** per asset; **gross exposure <= 100%**; remainder held as cash.

### 5.2 Execution and cost (next-open)

- Target weights submitted by the **close of day `t`** are executed at the **open of day
  `t+1`**. This next-open rule is what prevents look-ahead: you decide on day `t` information
  and trade at a price you could not have known when deciding.
- Transaction fee: **0.1%** of traded notional on both buys and sells. Fractional shares are
  allowed. There is no modeled slippage in the prototype **[TBC: slippage/impact for finals]**.

### 5.3 Action validation and repair (feeds M9)

Before execution, weights are repaired deterministically (`risk.sanitize_target_weights`):

| Violation code | Trigger | Repair |
| --- | --- | --- |
| `unknown_asset` | ticker not in the universe | dropped |
| `invalid_number` | non-numeric / non-finite weight | set to 0 |
| `short_position` | weight < 0 | clipped to 0 |
| `asset_cap` | weight > `max_asset_weight` | clipped to the cap |
| `gross_exposure` | sum of weights > `max_gross_exposure` | scaled down proportionally |

Any day whose action required repair counts toward **M9 (violation rate)**. The episode always
continues after repair.

### 5.4 Failure policy (retain previous weights)

If an agent crashes, times out, or returns no/invalid action, the server **retains the team's
previous target weights** (no fire-sale to cash), records the day as a violation, and
continues. The very first day with no previous weights defaults to all cash.

---

## 6. Evaluation Metrics and Scoring

Nine metrics across four dimensions, computed over the live evaluation period
(`metrics.compute_metrics`):

| ID | Metric | Dimension |
| --- | --- | --- |
| M1 | Cumulative return | Profitability |
| M2 | Daily win rate | Profitability |
| M3 | Sharpe ratio | Risk-adjusted |
| M4 | Sortino ratio | Risk-adjusted |
| M5 | Maximum drawdown | Risk |
| M6 | Value at Risk (95%) | Risk |
| M7 | Expected Shortfall (95%) | Risk |
| M8 | Turnover | Execution |
| M9 | Violation rate | Execution |

### 6.1 Winner selection (average rank)

Each metric is transformed so **lower is better** (return-type metrics M1-M4 are inverted).
For each metric, teams are ranked (1 = best). A team's final score is the **average of its
ranks across all nine metrics**; the **lowest average rank wins**. Ties are broken by higher
cumulative return, then lower maximum drawdown. This deliberately rewards agents that combine
profitability, disciplined risk management, and reliable execution rather than a single
high-variance bet. Computed by `engine.leaderboard`.

---

## 7. Reproducibility and Audit Trail

Every trading day, for every team, the server logs a complete, replayable record:

- the exact `observation` published (`data/observations/<date>.json`);
- the team's `decision_response` and the server's validation result
  (`submissions/<team>/<date>.json`);
- an audit block: `code_hash` (SHA-256 of the submission's `*.py`), `agent_version`,
  `declared_news_sources`, `compute_seconds`, the agent's own log output, and return code.

**Reproducibility check**: re-running a submission's `code/` on the logged observations must
reproduce the recorded weights. This is run for at least the top teams before final rankings
are confirmed. Non-reproducible or rule-violating submissions are disqualified.

---

## 8. Fairness and Anti-Cheating

- **No look-ahead.** The observation contains only point-in-time data; every timestamped item
  predates the decision cutoff, enforced by `security.assert_observation_point_in_time`. Teams
  must not use same-day-or-future prices, and any external news must predate the cutoff.
- **Public news only.** External data must come from public / open / free sources; private or
  paid feeds are forbidden unless explicitly allowed **[TBC]**.
- **Restricted LLMs.** If an agent uses an LLM, it must use one of the specified allowed models
  and versions, to prevent information leakage from newer or private models **[TBC: model list]**.
- **Same official data.** All teams receive identical official market, fundamental, and news
  data; only proprietary public news and the alpha code differ.

---

## 9. Timeline (from the proposal)

| Phase | Dates | What happens |
| --- | --- | --- |
| Preparation | 14 Sep - 4 Oct | Finalize dataset, platform, SDK, starter kit. |
| Development & Validation | 5 Oct - 23 Oct | Register; receive training data + SDK; submit to the online validation environment (2025 data) any number of times. |
| Official Competition | 26 Oct - 6 Nov | Daily: retrieve market data + public news, submit target weights, next-open execution. |
| Final Result & Presentation | 9 Nov - 17 Nov | Rankings announced 9 Nov; top-3 present at ICAIF 2026. |

---

## 10. How the daily cycle runs (operational)

### 10.1 The live competition - the website is a thin record + submission service

On each live trading day:

1. **Organizer / website** ingests and publishes the official market data + news panel, and
   sends each team its decision_request (shared panel + that team's portfolio state).
2. **Team, on its own machine** runs its `alpha/agent.py` on the request and **submits the
   resulting target weights** to the website (e.g. via `code/submit.py` / an HTTP POST). A
   missing or late submission triggers the retain-previous-weights policy (Section 5.4).
3. **Website** records the submission and validates/repairs the weights (Section 5.3).
4. **Organizer / website** at the next market open executes the queued weights with the 0.1%
   fee (Section 5.2), updates portfolio state, recomputes M1-M9, and refreshes the leaderboard
   and the public history.

**The website never runs participant code.** It publishes data, records submissions, keeps the
history, and computes execution/metrics from the submitted weights. In the demo,
`code/daily_cycle.py --source posted` performs steps 1, 3, and 4 and reads the weights each team
posted; in production the file drops become HTTP.

### 10.2 Reproducibility audit (post-competition, top teams only) - `--source code`

After the competition, the organizer runs each top team's frozen `alpha/` on the logged
requests in a sandbox and requires the produced weights to equal what the team submitted live
(Section 7). This is the ONLY time the organizer executes participant code. The same mechanism
also powers the optional online validation environment during the development phase, where a
team may self-test against a held-out 2025 window.
