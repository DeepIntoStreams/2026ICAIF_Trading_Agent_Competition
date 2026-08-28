# Data and Submission

We provide detailed instructions to help participants get started quickly, together with the
**Starter Kit** repository. This page covers the official data, the repository layout, the agent
interface and weight format, and how to submit.

## Official data

The **official data is sourced from Yahoo Finance and SEC EDGAR** and is identical for all
teams. It is provided through the Starter Kit and delivered as a single daily **observation**
per trading day, containing:

- **Market data (Yahoo Finance)** - daily open/high/low/close/volume for the 30-equity universe
  under a consistent adjusted-price convention, summarized as rolling-return, momentum, and
  volatility features.
- **Fundamental data (Yahoo Finance)** - quarterly profitability, growth, and leverage ratios,
  aligned point-in-time.
- **News (SEC EDGAR)** - a daily stream of company filings for the universe, each item
  timestamped to when it became available.

Data splits: **training (Jan 2020 - Dec 2024)** is released for development; the **validation
environment (2025)** mirrors the official protocol; the **live period** uses data released
during the competition.

## Additional data (allowed, but must be uploaded for audit)

Other than the official data above, teams **may download and use additional data of their own
choosing, provided it is publicly and freely accessible** - for example other public news
sources or public datasets. **Private, paid, or non-public feeds are not permitted.**

**Any additional data you use must be uploaded to the platform for audit.** The organizers use
it to reproduce your pipeline and verify that every input predates the daily decision cutoff (no
look-ahead) and comes only from allowed public sources. **Data that is used but not uploaded, or
that comes from a non-public source, disqualifies the submission.**

## Repository organization

Participants develop against the framework repository, which has three top-level directories:

| Directory | Owner | Contents |
| --- | --- | --- |
| `competition/data/` | organizer | the official data (Yahoo Finance + SEC EDGAR), read-only |
| `competition/code/` | organizer | the framework given to everyone - the SDK (agent API), the local backtester, the panel builder, and the execution/scoring engine. **Do not modify.** |
| `competition/alpha/` | **you** | your strategy - the **only** directory you write and submit |

Your `alpha/` directory must be organized as follows:

```
alpha/
  agent.py            # REQUIRED - defines class Agent(BaseAgent) with decide(observation)
  requirements.txt    # pinned dependencies (frozen for reproducibility)
  MANIFEST.json       # team_id, entrypoint, agent_version, python version, model + version
  model_weights.*     # OPTIONAL - trained parameters / checkpoint loadable by agent.py
  proprietary_news/   # OPTIONAL - any additional public data you use (also uploaded for audit)
```

## The agent interface (input and output)

`agent.py` defines a class `Agent` that subclasses `BaseAgent` and implements `decide`:

```python
class Agent(BaseAgent):
    agent_version = "my-alpha-1.0"
    def setup(self, universe, constraints): ...          # called once, before the first day
    def decide(self, observation) -> dict[str, float]:   # each day -> target weights
```

- **Input:** one `observation` per trading day - the day's market, fundamental, and news
  features plus your current portfolio state (weights, cash ratio, drawdown).
- **Output - the weights format:** `target_weights`, a JSON object mapping **ticker to weight**.
  Long-only (weight >= 0), **<= 0.10 per name**, **sum <= 1.00**; any unallocated capital is held
  as cash, and tickers outside the universe are rejected. For example:

```json
{"AAPL": 0.08, "MSFT": 0.07, "NVDA": 0.05, "JPM": 0.06}
```

Any internal design is allowed as long as this input/output contract is respected.

## Backtest and look-ahead check

**Look-ahead bias** - using any information dated after a day's decision cutoff - is the most
common way a trading result becomes invalid. Before submitting, check your own alpha with the
Starter Kit backtester. It runs your alpha over the historical data with the exact official
rules (next-open execution, 0.1% fee, 10% cap) and prints the same M1-M9 the leaderboard uses,
so your local score matches the official one.

```bash
# backtest your alpha over a window (prints M1-M9)
python competition/code/backtest.py --alpha competition/alpha \
    --data-root <data> --start 2026-06-05 --end 2026-06-18

# add the look-ahead audit
python competition/code/backtest.py --alpha competition/alpha \
    --data-root <data> --start 2026-06-05 --end 2026-06-18 --check-lookahead
```

It applies two look-ahead protections:

1. **Guarantee (always on).** Every daily observation handed to your alpha is verified
   point-in-time - it contains nothing dated after the cutoff. If your alpha uses only the
   observation, it cannot leak the future.
2. **Audit (`--check-lookahead`).** For sampled days it removes all data after that day and
   re-runs your decision; a clean decision must not change. Any day whose decision *does* change
   is flagged, so you can find and fix the leak.

**The backtest is a partial check, not the final word.** Passing it does not by itself qualify a
submission. During winner selection the organizers run **additional audits** - on the top teams,
over more days, and with checks beyond truncation-invariance (reproducibility of your submitted
weights, allowed data sources and models, and other integrity tests) - to prevent an unexpected
or ineligible win. Use the backtest to catch problems early, and expect a stricter review at the
end (see the **Evaluation** page).

## Submission

There are two submission modes, matching the two phases (see **Phases**).

### A. Validation submission - development phase (submit your code)

To test your agent on the held-out **2025** environment, package your `alpha/` directory and
submit it as a `.zip` containing `agent.py`, `requirements.txt`, `MANIFEST.json`, and any
`model_weights.*`. **Do not group the files into a subfolder before zipping**, as this causes
submission failure. **If you use Chrome/Edge above version 125.x, we recommend Firefox for
submissions.** The platform runs your code over the validation period with the official rules
(next-open execution, 0.1% fee, 10% cap) and returns your metric scores.

### B. Live submission - competition phase (submit your weights)

During the live phase you run your agent **on your own machine** each trading day and submit
**only the resulting weights** as a `submission.json` - a `decision_response` validated against
`decision_response.schema.json`:

```json
{
  "type": "decision_response",
  "protocol_version": "0.1",
  "team_id": "team_001",
  "session_date": "2026-10-27",
  "target_weights": {"AAPL": 0.08, "MSFT": 0.07, "NVDA": 0.05}
}
```

Submit before the daily deadline; the platform validates, executes at the next open, and updates
the leaderboard. **Your code is not uploaded during the live phase - only these weights.**

### Report and code (for prize eligibility)

To qualify as a top-ranked team, submit a **1-2 page report** on your methodology and results to
**tsg.icaif@gmail.com** before the deadline, and provide your full `alpha/` code for the
**reproducibility audit** on request - the organizers re-run it on the recorded observations and
confirm it reproduces the weights you submitted live. Only submissions that strictly follow the
required format are accepted for leaderboard evaluation.

## View results

Participants see their submissions in the **My Submissions** section and can inspect detailed
scores. On the **Results** page, the leaderboard shows each team's average rank across all
metrics. If multiple attempts are made, select the one to appear on the leaderboard via **Submit
to Leaderboard** - that entry is considered your final answer for the phase.

## Starter kit

We recommend using the illustrative notebook to kick off. It provides a standard pipeline with
four modules: **(1)** data access and the observation format; **(2)** baseline agents (rule-based,
learning-based, and LLM examples); **(3)** the local backtest and evaluation workflow - the same
engine that scores the leaderboard, so your local numbers match the official ones; and **(4)**
submission formatting.

**Offline evaluation.** Assess your agent locally on the historical data and run the look-ahead
check (see **Backtest and look-ahead check** above) before submitting. The official online
evaluation uses held-out and newly released data to ensure fairness and prevent overfitting, and
the organizers apply further audits during winner selection.
