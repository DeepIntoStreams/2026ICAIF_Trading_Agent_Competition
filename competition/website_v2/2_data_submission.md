# Data and Submission

This page summarizes what data we provide, what data you may add, and how the competition runs
day to day. The technical detail - the agent interface, feature examples, local backtesting, and
the client that submits your daily decision - lives in the **Starter Kit**.

## Official data

For a fixed universe of **30 U.S.-listed equities**, identical for all teams, the organizers
provide two data types:

- **Market data** - daily **open, high, low, close, and volume (OHLCV)** for each stock and
  trading day.
- **Fundamental data** - quarterly company fundamentals (profitability, growth, and revenue, etc.).

### Universe

| Sector group | Stocks |
| --- | --- |
| Technology | AAPL, MSFT, NVDA, INTC, CRM |
| Finance | JPM, BAC, GS, V, PYPL |
| Healthcare | LLY, JNJ, UNH, PFE, TMO |
| Consumer | AMZN, TSLA, WMT, NKE, KO |
| Industrial & Energy | CAT, GE, BA, XOM, CVX |
| Communication & Utilities | GOOGL, META, DIS, T, NEE |

### Data periods

- **Training** - multi-year historical data for developing your agent. **(Jan 1 2021 - Jan 1 2025)**
- **Validation** - a held-out period for testing your agent against our system.
  **(Jan 2 2025 - Jan 1 2026)**
- **Live** - real market data released each trading day during the competition.**(Oct 26 2026 - Nov 6 2026)**

### Availability

Each trading day, **after the previous session's close**, your agent **connects to the official
server and receives** the observation for the upcoming decision - market price-volume and
fundamental data through that close. Your decision may use this official data together with any additional data you have
collected, provided it was available **at or before that day's 9:00 AM ET cutoff**. Nothing dated
after the cutoff may be used.

## Additional data

Beyond the official data, you may collect and use **additional public information of your own**.
- **Sources must be open and freely accessible to everyone** - no private, paid, or restricted
  feeds. Recommended public sources, as a starting point: **Yahoo Finance** (financial news) and
  **SEC EDGAR** (company filings and disclosures). You may use any other source that meets the
  same open-access requirement.
- Any additional data you use may need to be provided at the end of the competition for
  reproducibility checking (see **Final submission**).

## Agent interface

You build a trading agent against the interface provided in the Starter Kit. At a high level:

- **Each trading day, the agent receives** that day's official observation (market and
  fundamental data through the previous close) together with its current portfolio state.
- **The agent returns** target portfolio weights over the 30-stock universe. Weights are
  long-only, and any unallocated capital is held as cash.

Exact input/output formats and example agents are in the Starter Kit.

## How the live competition works

During the live phase the competition runs automatically through a client that connects your
agent to the official server, one decision per trading day. It is **not** a manual file upload as
in previous competitions.

1. **After the previous day's close**, your agent **connects to the official server** and
   receives the day's **observation** (market price-volume and fundamental data through that
   close) and your **current portfolio state**.
2. Your agent computes **target portfolio weights** and returns them **before the 9:00 AM ET
   cutoff**; the official client submits the decision automatically.
3. The submitted weights are **executed at that day's market open (9:30 AM ET)** and scored.

- **Daily deadline:** return your decision by the **9:00 AM ET** cutoff each trading day.
- **Missed decision:** if your agent returns nothing for a day, your **previous weights are
  carried forward**.

The Starter Kit provides the client that runs this daily loop for you.

## Validation

Validation lets you confirm that your agent **connects to our system and runs correctly** - that
your interface, submission flow, and the evaluation pipeline work end to end before the live
phase.

- It runs on a **held-out validation period**, not the live market.
- You may validate **multiple times per day** (see the limit in **Terms**), and you see your
  results for each attempt.
- **Validation does not count toward the final ranking**, and you do **not** submit your code to
  the organizers at this stage.

## Final submission

After the live phase ends (Nov 6), there is a short **final-submission window**, closing before
the final leaderboard is published (Nov 10), in which top teams provide the materials needed to
verify their results:

- a **video demo** of their solution; and
- their **code, any trained models, and any additional data used**, for the **reproducibility
  check**.

**Teams that do not submit the required materials, or whose results cannot be reproduced by the
organizers, do not receive a final ranking.**

## Results

You see your results on the competition platform, with validation and the live phase shown
separately:

- **Validation results** tell you whether your agent connects to and runs on our system; they
  are feedback only.
- The **live leaderboard** is built from your agent's actual daily decisions over the live phase.
  There is no single submission file - the sequence of daily decisions forms your trading record.
- The **final leaderboard** is published only after the live phase ends, the final-submission
  window closes, and reproducibility checking is complete.

The metrics and ranking method are defined on the **Evaluation** page.

## Starter Kit

The **Starter Kit** holds the technical detail - the agent interface and example agents, the
observation and output formats, and the client used in the live phase - and lets you test the
complete workflow end to end:

- **local backtesting** on the training data;
- **connecting to the validation system**;
- **using the live agent interface**; and
- **automatic daily decision submission**.

Start there to build and test your agent.
