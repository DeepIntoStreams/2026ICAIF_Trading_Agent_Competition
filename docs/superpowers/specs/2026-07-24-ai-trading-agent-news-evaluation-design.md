# AI Trading Agent News Evaluation Design

Date: 2026-07-24

## Goal

Build a simple but competition-ready evaluation framework for AI trading agents. The framework must simulate a realistic US equity daily workflow over a two-week horizon, add stock-related news as first-class point-in-time data, and report the agreed profitability, risk-adjusted, risk, and execution-quality metrics for three baseline agents.

This design explicitly allows agents to see ticker symbols, company names, and raw news text. The strict safety boundary is no future data leakage: every market, fundamental, and news field visible to an agent must have been available at or before the simulated decision cutoff.

## Current Repo Context

The workspace is on branch `v2.0`. The working tree currently shows many tracked files deleted under `src/`, `configs/`, `scripts/`, `tests/`, and `docs/`, plus an untracked root-level `portfolio_agent/` package. The root-level package appears to mirror the deleted `src/portfolio_agent/` package, while `pyproject.toml` still expects packages under `src`.

Implementation will preserve the repository's configured `src` layout. The code package used by tests and scripts is `src/portfolio_agent/`. The untracked root-level `portfolio_agent/` directory should not be deleted during this MVP unless the user separately approves a package-layout migration.

## News Provider Decision

Use Finnhub Company News as the primary MVP news source.

Finnhub is the best fit because its Company News endpoint supports company-specific news for North American companies and the free tier documents one year of historical company news plus new updates. For a live competition run, the collector should poll this endpoint during the trading day and record when each item is first observed.

Important limitation: Finnhub free REST polling is suitable for same-day intraday updates, but it is not a hard real-time feed with a guaranteed latency SLA. Finnhub's WebSocket real-time news feed is a premium capability. For competition fairness, the evaluator must treat `first_seen_at_utc` as the availability boundary, not assume that `published_at_utc` was visible to the agent if the system had not observed it yet.

Supplemental sources:

- SEC EDGAR APIs: use for no-key regulatory filings and material event metadata. These are not general market news, but they are high-signal, free, and point-in-time friendly.
- GDELT 2.0: use as a no-key broad web-news supplement. It updates frequently, but ticker mapping is weaker, so it should not be the primary source.
- Alpha Vantage News and Sentiment: use as a validation or enrichment source, not as the primary intraday source, because the free request limit is too low for 30 tickers across a trading day.
- Alpaca news: add as an optional provider only after an entitlement probe passes for the user's account. Do not make it a required dependency for the MVP.
- NewsAPI developer tier: exclude from competition ingestion because free developer access has material restrictions and delayed access that do not match live intraday trading.

## Canonical News Schema

Every raw provider payload must be persisted before normalization. Normalized records use this schema:

```python
class NewsRecord:
    provider: str
    provider_news_id: str
    published_at_utc: datetime
    fetched_at_utc: datetime
    first_seen_at_utc: datetime
    available_at_utc: datetime
    tickers: list[str]
    company_names: list[str]
    headline: str
    summary: str
    source: str
    url: str
    content_hash: str
    raw_path: str
```

Availability rules:

- Live collection: `available_at_utc = first_seen_at_utc`.
- Historical backfill: default strict mode excludes records without a trustworthy `first_seen_at_utc`. A relaxed validation mode may set `available_at_utc = published_at_utc`, but every run using that mode must mark `historical_backfill_mode = true` in the manifest and metrics output.
- Duplicate records collapse by `(provider, provider_news_id)` first, then by `content_hash`.
- If the same story appears for multiple tickers, keep one raw story and attach all mapped tickers.
- Raw JSONL and normalized JSONL are immutable once written for an official run.

## Trading-Day Simulation

The MVP simulates 10 NYSE regular trading sessions, which is a two-week horizon under normal conditions. Use the NYSE calendar for holidays and early closes.

Regular session clock in America/New_York:

- Open: 09:30
- Decision cutoff: 15:50
- Close and mark: 16:00

For early-close days, shift the decision cutoff to 10 minutes before the official close.

Daily event sequence:

1. Build the premarket observation from all approved prior data and news with `available_at_utc <= market_open_utc`.
2. Emit `market_open` with open prices, previous close features, current cash, current holdings, and point-in-time historical features.
3. Stream or batch all intraday news events where `market_open_utc < available_at_utc <= decision_cutoff_utc`, ordered by `available_at_utc`, provider, then stable record id.
4. Run a final news poll before cutoff in live mode. Recommended target: 15:45 ET for a regular close.
5. Ask the agent for target portfolio weights at `decision_cutoff_utc`.
6. Repair invalid actions through the existing feasibility and risk layer, recording all repairs.
7. Execute at the official close price as a market-on-close approximation.
8. Apply fees and optional slippage.
9. Mark the portfolio at close and write daily NAV, trades, actions, news seen, repairs, and audit records.
10. Carry post-cutoff news to the next session's premarket observation.

The MVP uses daily OHLCV data because that matches the current dataset. At decision time, the agent may see today's open, prior historical bars, and allowed news, but not today's high, low, close, adjusted close, or volume unless those fields are explicitly available before cutoff through a separate intraday market feed. Close prices are used only by the execution and marking engine after the action is submitted.

## Evaluation Window

Evaluation horizon must be configurable. The default MVP uses `horizon_trading_days = 10`, which corresponds to a two-week horizon under normal NYSE calendars. The evaluator must also support explicit `start_date` and `end_date` overrides so competition planners can compare 5-day, 10-day, 20-day, and multi-window runs without changing code.

If `start_date` and `end_date` are supplied, they control the selected NYSE sessions. If only `end_date` and `horizon_trading_days` are supplied, the evaluator selects the last `horizon_trading_days` valid sessions ending on `end_date`. If no dates are supplied, the evaluator uses the latest `horizon_trading_days` sessions available in the market dataset.

Initial functional replay should default to the last 10 trading sessions already present in the local one-year stock dataset:

- 2026-06-05
- 2026-06-08
- 2026-06-09
- 2026-06-10
- 2026-06-11
- 2026-06-12
- 2026-06-15
- 2026-06-16
- 2026-06-17
- 2026-06-18

This replay validates the mechanics, agent interfaces, metrics, and audit outputs. It must not be advertised as a strict no-leakage benchmark unless all news records for that window have trustworthy `first_seen_at_utc`.

The first official competition-quality dataset should be captured shadow-live for a future 10-session window with immutable raw provider payloads and collector timestamps.

## Leakage Prevention

The evaluator, not the agents, owns all data retrieval. During evaluation, agents must not call external network APIs, read raw future files, or discover data outside the observation object.

Required controls:

- All data rows include an `available_at_utc`.
- Observation builder filters every record by `available_at_utc <= current_event_time_utc`.
- News text is exposed only through the observation after filtering.
- Same filtered observation and news ordering are used for all agents.
- Raw provider payloads, normalized news, market data, config, code commit, and random seeds are hashed into a run manifest.
- A sentinel news item after cutoff is included in tests and must never appear before the next allowed event.
- Historical-backfill runs are explicitly labeled and excluded from official leaderboard claims.
- LLM baseline uses only the local OpenAI-compatible endpoint. It must not use hosted remote LLM APIs during evaluation.

## Agent Interface

Use an event-style interface so all baselines can react to market and news events while sharing a common decision cutoff.

```python
class TradingAgent:
    def reset(self, context: EvaluationContext) -> None:
        ...

    def observe(self, event: MarketEvent | NewsEvent) -> None:
        ...

    def decide(self, observation: DecisionObservation) -> TargetWeights:
        ...
```

`DecisionObservation` contains:

- session date and event timestamps
- investable universe with tickers and company names
- open prices and allowed historical market features
- allowed fundamentals
- current portfolio state
- news visible since the previous decision, with raw headline, summary, source, URL, ticker mapping, and timestamps
- config constraints such as max position size, cash policy, and fee model

## Experiment Configuration Interface

The competition framework must expose experiment knobs through `configs/evaluation.yaml` and matching CLI overrides. These values are variables for competition planning, not constants embedded in agent or evaluator code.

Required configurable fields:

- `evaluation.horizon_trading_days`
- `evaluation.start_date`
- `evaluation.end_date`
- `evaluation.decision_minutes_before_close`
- `evaluation.initial_cash`
- `evaluation.fee_rate`
- `evaluation.slippage_bps`
- `constraints.max_asset_weight`
- `constraints.max_gross_exposure`
- `news.providers`
- `news.max_items_per_decision`
- `news.include_raw_text`
- `news.historical_backfill_mode`
- `agents.enabled`
- `agents.hybrid.news_weight`
- `agents.hybrid.rebalance_frequency`
- `agents.ppo.news_beta`
- `agents.ppo.news_count_gamma`
- `agents.llm.base_url`
- `agents.llm.api_key`
- `agents.llm.model`
- `agents.llm.temperature`
- `agents.llm.max_tokens`
- `agents.llm.timeout_seconds`
- `agents.llm.rebalance_frequency`

The default LLM values remain:

- `agents.llm.base_url = "http://localhost:8000/v1"`
- `agents.llm.api_key = "unused"`
- `agents.llm.model = "google/gemma-4-31B-it"`
- `agents.llm.temperature = 0`

Every run manifest must record the resolved config after CLI overrides. This allows fast experiments across horizon length, news limits, LLM model choice, and cost assumptions while keeping leaderboard runs reproducible.

## Baseline Agents

### Hybrid Rule News Agent

Extend the existing hybrid rule baseline with a news score. The score should combine:

- recency weight based on `available_at_utc`
- ticker relevance
- simple local sentiment or event-category scoring from headline and summary
- source count and duplicate compression

Default allocation signal:

```text
score = 0.55 * technical_score + 0.25 * fundamental_score + 0.20 * news_score
```

If no news is visible for a ticker, redistribute the news weight across technical and fundamental scores.

### News-Tilted PPO Agent

Keep the existing PPO checkpoint usable for the MVP. Because the current PPO state vector was trained without news and no aligned point-in-time historical news dataset exists, do not retrain PPO as part of the first implementation.

Instead, wrap the PPO allocation with a deterministic news tilt:

```text
adjusted_logit_i = log(base_weight_i + epsilon) + beta * news_sentiment_i + gamma * log(1 + news_count_i)
```

Then softmax and project the result into the same risk and feasibility constraints as every other agent. Record the pre-tilt and post-tilt weights for auditability.

### LLM News Allocation Agent

Use the user's required OpenAI-compatible local client and model:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")

response = client.chat.completions.create(
    model="google/gemma-4-31B-it",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

Evaluation settings:

- `base_url = "http://localhost:8000/v1"`
- `api_key = "unused"`
- `model = "google/gemma-4-31B-it"`
- deterministic decoding for leaderboard runs, using `temperature = 0` when supported by the local server
- bounded prompt containing ticker, company name, current portfolio, allowed market features, allowed fundamentals, and raw visible news
- cap the prompt to the top 40 relevant news items per decision, while preserving at least one visible item per ticker with news when possible
- require strict JSON target weights as the model output
- on timeout, parse failure, or infeasible output, repair the action, record the violation, and fall back to the previous valid target weights when repair cannot produce a valid target

## Portfolio And Execution Rules

Default MVP settings:

- Initial capital: USD 1,000,000
- Universe: the current 30-stock local evaluation universe
- Positioning: long-only equities plus cash
- Max asset weight: 30 percent
- Gross exposure: at most 100 percent
- Shorting: disabled
- Fractional shares: enabled
- Fee: 10 bps of traded notional
- Slippage: disabled by default, configurable for ablations
- Risk-free rate for Sharpe and Sortino: 0

Actions are target weights, not share quantities. The execution engine converts target weights into close-price fills after the agent submits a decision.

## Metrics

Let `V_t` be end-of-day portfolio value after close execution and fees. Let `r_t = V_t / V_{t-1} - 1` for each evaluated session.

M1: Cumulative Return

```text
V_T / V_0 - 1
```

M2: Daily Win Rate

```text
count(r_t > 0) / number_of_trading_days
```

Flat days do not count as wins.

M3: Sharpe Ratio

```text
sqrt(252) * mean(r_t - rf_daily) / std(r_t - rf_daily, ddof=1)
```

Return null when fewer than 2 daily returns exist or volatility is zero.

M4: Sortino Ratio

```text
sqrt(252) * mean(r_t - rf_daily) / downside_deviation
```

Downside deviation is computed only from negative excess returns. Return null when there is no downside volatility.

M5: Maximum Drawdown

```text
max(1 - V_t / running_peak_t)
```

Report as a non-negative loss magnitude.

M6: Value at Risk

Use one-day historical 95 percent VaR over the evaluation returns:

```text
max(0, -quantile(r_t, 0.05))
```

M7: Expected Shortfall

Use one-day 95 percent historical ES:

```text
mean(-r_t for r_t <= quantile(r_t, 0.05))
```

Return null if the tail set is empty.

M8: Turnover and Cost Rate

```text
turnover = sum(abs(traded_notional_t)) / average_daily_nav
cost_rate = sum(transaction_costs_t) / average_daily_nav
```

Also report per-day turnover and per-day cost for diagnostics.

M9: Violation Rate

```text
count(days_with_any_action_repair) / number_of_trading_days
```

Repairs include invalid JSON, missing weights, negative weights in long-only mode, gross exposure above 100 percent, max-position violations, NaN or infinite values, unknown tickers, and weights that cannot be projected into the feasible set without modification.

Risk metric caveat: a 10-day evaluation window is too small for statistically stable Sharpe, Sortino, VaR, and ES. The framework must output `sample_size = 10` and `low_sample_warning = true` for each two-week run. Official competition ranking should aggregate across multiple hidden two-week windows instead of over-interpreting one short run.

## Outputs

Per agent:

- `metrics.json`
- `daily_nav.csv`
- `trades.jsonl`
- `actions.jsonl`
- `news_seen.jsonl`
- `violations.jsonl`
- `run_manifest.json`

Cross-agent:

- `comparison.csv`
- `comparison.json`
- `news_coverage_report.json`

The manifest includes:

- git commit or dirty-worktree marker
- config hash
- market data hashes
- raw news file hashes
- normalized news file hashes
- agent name, version, and seed
- LLM endpoint and model name for the LLM baseline
- historical backfill mode flag

## Proposed Code Structure

Core package:

- `portfolio_agent/news/models.py`: typed news records and provider responses
- `portfolio_agent/news/providers/base.py`: provider protocol and fetch windows
- `portfolio_agent/news/providers/finnhub.py`: Finnhub REST collector
- `portfolio_agent/news/providers/sec_edgar.py`: SEC supplement collector
- `portfolio_agent/news/providers/gdelt.py`: GDELT supplement collector
- `portfolio_agent/news/normalizer.py`: provider-specific normalization
- `portfolio_agent/news/store.py`: raw JSONL, normalized JSONL, dedupe, and manifest hashing
- `portfolio_agent/news/sentiment.py`: deterministic local headline and summary scoring
- `portfolio_agent/events.py`: market open, news, decision, close event types
- `portfolio_agent/market_calendar.py`: NYSE sessions, opens, closes, early closes
- `portfolio_agent/execution.py`: target-weight execution, fees, and portfolio accounting
- `portfolio_agent/observation.py`: point-in-time observations with ticker, company name, and news
- `portfolio_agent/evaluator.py`: event-loop orchestration
- `portfolio_agent/metrics.py`: M1 through M9 metrics
- `portfolio_agent/agents/hybrid_rule.py`: news-aware hybrid rule baseline
- `portfolio_agent/agents/ppo_portfolio.py`: news-tilted PPO wrapper
- `portfolio_agent/agents/llm_allocation.py`: local Gemma news allocation baseline

Scripts:

- `scripts/collect_news.py`: live and backfill provider collection
- `scripts/backfill_news.py`: historical provider backfill for mechanics validation
- `scripts/run_evaluation.py`: one-command evaluation across three baselines
- `scripts/validate_dataset.py`: leakage, date coverage, and news availability checks

Config:

- `configs/evaluation.yaml`: universe, dates, fees, constraints, news providers, LLM endpoint, and output paths

Tests:

- cutoff filtering and sentinel leakage tests
- news dedupe and normalization tests
- NYSE event ordering tests
- close execution and fee accounting tests
- M1 through M9 metric fixture tests
- LLM JSON parsing, timeout, and repair tests
- baseline news sensitivity tests
- reproducibility tests for config hash, seed, and output equality

## Implementation Order

1. Restore the configured `src/portfolio_agent/` package layout and runnable imports without deleting the untracked root-level package.
2. Add tests for point-in-time news filtering and daily event ordering.
3. Add news models, normalizer, store, and Finnhub provider.
4. Add SEC and GDELT supplemental providers behind config flags.
5. Add NYSE calendar and event loop for open, intraday news, cutoff decision, and close execution.
6. Update observations to expose ticker, company name, and allowed raw news.
7. Update metrics to exactly report M1 through M9.
8. Update the hybrid, PPO, and LLM baselines to consume news.
9. Add one-command evaluation and dataset validation scripts.
10. Run the last-10-session historical replay and mark it as mechanics validation.
11. Add shadow-live collection instructions for the first official no-leakage dataset.

## Acceptance Criteria

- Finnhub collector can poll current-day company news before US market close and persist raw plus normalized records with `first_seen_at_utc`.
- Evaluation can run the default 10 NYSE sessions, and can also run another configured horizon without code changes.
- Agents see ticker, company name, and raw allowed news, and never see post-cutoff market or news data.
- Sentinel future-news test proves cutoff isolation.
- All three baselines produce valid action logs, trades, daily NAV, news seen, violations, and metrics.
- Metrics output includes M1 through M9 with the exact definitions in this spec.
- LLM baseline uses `http://localhost:8000/v1`, `api_key="unused"`, and `model="google/gemma-4-31B-it"`.
- LLM endpoint, model, temperature, token limit, timeout, and rebalance frequency can be changed through config or CLI overrides without changing code.
- Same config, data hashes, code state, and seed reproduce the same non-LLM outputs. LLM outputs are deterministic when the local server honors deterministic decoding.
- Historical replays are clearly labeled when strict first-seen timestamps are unavailable.
- Official leaderboard runs are based on shadow-live captured news or another dataset with trustworthy availability timestamps.

## Out Of Scope For The MVP

- Intraday bar execution using minute-level prices.
- Training a new PPO model with historical point-in-time news embeddings.
- Full-text article scraping beyond provider-provided headlines and summaries.
- Paid real-time news feeds.
- Composite ranking weights across metrics.
- Options, short selling, leverage, margin, and market-impact modeling.
