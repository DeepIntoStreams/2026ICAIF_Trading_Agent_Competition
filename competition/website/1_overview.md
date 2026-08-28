# ACM ICAIF 2026 Trading Agent Competition

This competition is featured in the competition track of the **7th** ACM International
Conference on AI in Finance (ICAIF 2026).

Trading in equity markets is a continuous stream of decisions: how much capital to allocate,
when to rebalance, and how much risk to bear as market conditions and portfolio states evolve.
Under noisy signals, transaction costs, portfolio constraints, and shifting regimes, trading
performance depends on the quality of these repeated decisions. This makes trading a natural
setting for autonomous agents that map market observations directly to portfolio actions - and
one where evaluation is hard, because most benchmarks rely on historical backtests where the
data is no longer genuinely unseen. Participants are challenged to build an agent that manages
a portfolio of the top U.S. equities and is evaluated **live**, on newly released market data,
under realistic costs and risk constraints. This competition bridges agent design and
portfolio management, encouraging robust, auditable systems for algorithmic trading and risk
management.

## Overview

Developing agents that turn heterogeneous market information - prices, fundamentals, and news -
into disciplined portfolio decisions, and evaluating them under realistic execution and risk
constraints, is of significant value. Such agents advance autonomous trading by combining
predictive skill with risk control, ensuring greater applicability in algorithmic trading,
treasury management, and market-microstructure research.

## Competition Description

The goal of this competition is to develop robust trading agents that manage a long-only
portfolio over a fixed universe of U.S. equities, incorporating heterogeneous information
sources. On each trading day, given the day's market data, fundamentals, and news, an agent
returns target portfolio weights for the next trading day.

**Dataset.** The dataset covers a fixed universe of **30** U.S.-listed equities across six
sector groups (technology, finance, healthcare, consumer, industrial & energy, communication &
utilities), spanning several years. For each asset and trading day, three information types are
provided: **(1) market data** - daily open, high, low, close, and volume under a consistent
adjusted-price convention, summarized as rolling returns, momentum, and volatility features;
**(2) fundamental data** - quarterly profitability, growth, and leverage ratios, aligned
point-in-time; and **(3) news** - a daily stream of public financial news for the universe,
timestamped to when it became available. All data is delivered point-in-time: nothing is dated
after the daily decision cutoff.

**Task.** Each agent implements the mapping πθ : (Oₜ, Sₜ) → wₜ₊₁. Oₜ is the information available
on day t - the full history of prices, fundamentals, and news (every
item whose availability timestamp is at or before the cutoff), together with any state or
memory the agent carries across days. It may use **nothing dated after the cutoff** (no
look-ahead). Sₜ is the agent's portfolio state (current weights, cash ratio, drawdown), and
wₜ₊₁ are the target weights executed on day t+1. The task is inherently sequential: each action
changes the portfolio through execution and cost, and returns compound over the horizon. Weights are long-only, capped at
**10%** per asset with gross exposure ≤ **100%**; the remainder is held as cash. There is no
restriction on how πθ is built (expert rules, reinforcement learning, forecasting, LLMs, or any
combination), provided it implements the standardized interface.

**Evaluation and starter kit.** Agents are evaluated over the live period on a multi-metric
framework spanning profitability, risk-adjusted performance, risk management, execution
quality, and integrity/reproducibility, combined into a weighted-average rank and a two-stage
winner selection. **The full metric definitions, the weighting, and the winner-selection
procedure are detailed on the Evaluation page.** To accelerate onboarding, we provide a
**Starter Kit** covering: (1) data access and the observation format; (2) baseline agents built
several ways; (3) the local backtest and evaluation pipeline (identical to the official engine);
and (4) submission formatting.

## Potential Impact and Learning Opportunity

Beyond the leaderboard, robust trading agents evaluated under realistic costs and risk
constraints support the development of more resilient, auditable automated trading systems. A
shared environment with common official data, rules, and costs makes studies of trading agents
easier to relate and reproduce, while live evaluation on newly released data provides a fairer
assessment than a fixed backtest. By releasing a curated, standardized environment, the
competition encourages reproducible research and broadens access to high-quality financial-AI
tooling.

## Award

The top-ranked teams will be invited to present their solutions at **ACM ICAIF 2026**. **Any
travel support and additional recognition will be announced on the platform.**

## Key dates (GMT+8)

- **Competition launch (development & validation opens): Oct 1, 2026**
- **Live competition begins: Oct 27, 2026**
- **Competition end (final live trading day): 23:59, Nov 7, 2026**
- **Winner announcement: Nov 10, 2026**

## Registration

To register, refer to the **Submission** instructions on the **Data** page. Participants may
join as a team of up to **five** members. To create a team, use one personal account to create
an organization via the **Create Organization** button in the account drop-down menu, fill in
your team's details, then open your organization page and click **Edit Organization** to invite
teammates. When submitting, choose **Submit as** and select your organization. We strongly
encourage participants to register and submit using their **institutional email addresses**.

## Organizers

- Xinyu Xi (National University of Singapore)
- Yifan Bao (National University of Singapore)
- Ha Cong Nga (National University of Singapore)
- Qiang Wang (National University of Singapore)
- Yihao Ang (National University of Singapore)
- Anthony K. H. Tung (National University of Singapore)
- Yueju Han (South China University of Technology)
- Xin Zhang (South China University of Technology)
- Hao Ni (University College London)
- Lukasz Szpruch (University of Edinburgh)

## Contact

For any questions about this competition, please contact us at **tsg.icaif@gmail.com**.
Participants are welcome to join the **Discord community** for live discussion during the
competition.

## Citation

Xi et al. 2026. ACM ICAIF 2026: Trading Agent Competition,
**https://hackathon2.deepintomlf.ai/competitions/[ID]**

Xi et al. ICAIF 2026: Trading Agent Competition Starter Kit,
**https://github.com/DeepIntoStreams/2026ICAIF_Trading_Agent_Competition**
