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
a portfolio of the popular U.S. equities and is evaluated **live**, on newly released market data,
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
sources. On each trading day, given the previous days' market data, fundamentals, and news, an agent
returns target portfolio weights for the next trading day.

**Dataset.** The competition covers a fixed universe of **30** U.S.-listed equities across six
sector groups (technology, finance, healthcare, consumer, industrial & energy, communication &
utilities), spanning several years. The organizers **provide** two data types for each asset and
trading day: **(1) market price-volume data** - daily open, high, low, close, and volume, with
derived features such as rolling returns, momentum, and volatility; and **(2) fundamental data** -
quarterly profitability, growth, and leverage ratios. **News is not provided by the organizers**;
participants may add public financial news at their own discretion, from the public sources we
recommend on the **Data** page.

**Task.** Formally, on each trading day t+1 the agent observes Oₜ = (Xₜ, Zₜ, Nₜ) - the market,
fundamental, and news information available up to the cutoff time (**9:00 AM ET**) - together
with its portfolio state Sₜ, comprising current weights, cash ratio, and drawdown. Each agent πθ
implements the mapping πθ : (Oₜ, Sₜ) → wₜ₊₁ ∈ W, where wₜ₊₁ are the target portfolio weights
submitted at the cutoff and executed at the open of day t+1, and W is the feasible set defined by
the trading constraints. The task is inherently sequential: each action affects the portfolio
state through execution and costs, and returns compound over the evaluation horizon. There is no
restriction on how πθ is built, provided it implements the above mapping. Weights are long-only,
capped at **10%** per asset with gross exposure ≤ **100%**; the remainder is held as cash.

**Evaluation and starter kit.** Agents are evaluated over the **live period** on a multi-metric
framework spanning profitability, risk-adjusted performance, risk management, and execution
quality. Teams are ranked separately under each metric (M1-M9)
and the metric ranks are averaged to obtain the overall ranking. 
**Final-ranking eligibility requires completing the required final materials (a video demo, plus
code and models and data) and passing the organizers' reproducibility check** (see the Data and Terms
pages). **The full metric definitions and the ranking procedure are detailed on the Evaluation
page.** To accelerate onboarding, we provide a
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

The top winners / winning teams of the hackathon will be invited to
present their work at ACM ICAIF 2026, the 7th ACM International Conference on AI in Finance, taking place November 14-17, 2026 at Bocconi University in Milan, Italy.

## Key dates (US Eastern Time)

- **Competition launch (development & validation opens): Oct 1, 2026**
- **Live competition begins: Oct 26, 2026** (first trading day, 9:30 AM ET market open)
- **Competition end (final live trading day): Nov 6, 2026**
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

X. Xi, Y. Bao, H.C. Nga, Q. Wang, Y. Ang,  A. K. H. Tung, Y. Han, X. Zhang, H. Ni, and L. Szpruch.,
**https://hackathon2.deepintomlf.ai/competitions/[ID]**

X. Xi, Y. Bao, H.C. Nga,  Q. Wang, Y. Ang, A. K. H. Tung, Y. Han, X. Zhang, H. Ni, and L. Szpruch., **https://github.com/DeepIntoStreams/2026ICAIF_Trading_Agent_Competition**
