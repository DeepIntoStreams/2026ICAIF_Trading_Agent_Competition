# AI Trading Agent Competition Feasibility Prototype

This repository is a feasibility prototype for an AI trading agent competition.
It provides a local evaluation framework, three baseline agents, point-in-time
news ingestion utilities, configurable experiment settings, reproducibility
manifests, and detailed per-agent logs.

The code in this repository is not yet the final competition platform. Its
current purpose is to test whether the proposed evaluation protocol is
practical, whether news can be integrated safely, how the baselines behave, and
what the eventual SDK/server interface should look like.

## Important context for the next maintainer

The file `2026_ICAIF_trading_agent_competition.pdf`, if included with this
handoff, is the earlier competition proposal draft. That proposal described a
competition where teams submit containerized agents, the organizers run all
submitted code on the organizer server, and all participants receive the same
processed market and news streams.

After the discussion last Wednesday, the working direction changed:

1. Final evaluation should no longer require collecting every team's code and
   running all submissions on the organizer server. Instead, the organizers
   should run a live, multi-week evaluation that follows real U.S. market days:
   the market opens when the U.S. market opens, closes when the U.S. market
   closes, and uses the actual market data for that day. Participants should
   connect their agents to the organizer server through an SDK/API before the
   daily decision deadline, submit target portfolio weights, and let the
   organizer server compute execution, portfolio accounting, and metrics. Only
   the final top-ranked teams, for example the top 5 or top 10, are expected to
   submit runnable code for reproducibility checks.

2. News has been added to the feasibility study. For the formal competition,
   the current direction is that organizers may not provide news directly.
   Participants may collect and use public/open/free sources, but should not use
   private or paid proprietary sources unless the rules later explicitly allow
   them. This prototype still includes organizer-side news ingestion because we
   need to test whether adding news is feasible and whether it changes baseline
   behavior.

3. The official number of evaluation days is not finalized. The current default
   is 10 trading days, which approximates two calendar weeks, but this should be
   stress-tested with alternatives such as 5, 10, 14, and 20 trading days.

4. The competition will likely prescribe the LLM version or a small set of
   allowed LLM versions. In this baseline, the LLM agent uses an OpenAI-compatible
   local server:

   ```python
   from openai import OpenAI

   client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
   response = client.chat.completions.create(
       model="google/gemma-4-31B-it",
       messages=[{"role": "user", "content": "Hello!"}],
   )
   print(response.choices[0].message.content)
   ```

5. Stocks are no longer anonymized in the current direction. Agents are allowed
   to see ticker symbols and company names. The main security boundary is now
   strict prevention of future-data leakage.

## Current prototype at a glance

The current local evaluator simulates a daily U.S. equity trading process:

1. Select a configurable number of trading sessions.
2. At market open, expose same-day open prices and prior historical features.
3. Insert news records whose `available_at_utc` is no later than the decision
   cutoff.
4. Ask the agent for target portfolio weights before the close.
5. Repair invalid actions if needed.
6. Execute the sanitized target portfolio at the same day's close price as a
   market-on-close approximation.
7. Update cash, shares, fees, NAV, daily return, and risk metrics.
8. Save detailed logs, including the full JSON event log for each agent.

This is a daily-bar feasibility approximation. It does not yet implement the
future production SDK/server, authentication, participant registration, live
job scheduling, real exchange holiday/early-close calendars, or full
leaderboard operations.

## Repository layout

Use `src/portfolio_agent` as the source of truth. If a root-level
`portfolio_agent/` directory appears in a local workspace, treat it as a local
duplicate or generated artifact unless it has intentionally been made part of a
future package-layout migration.

### Top-level files and directories

| Path | Purpose |
| --- | --- |
| `README.md` | This handoff and usage guide. |
| `pyproject.toml` | Python package metadata. The installable package is loaded from `src/`. |
| `configs/evaluation.yaml` | Main configurable experiment file: horizon, costs, constraints, news settings, and agent settings. |
| `2026_ICAIF_trading_agent_competition.pdf` | Earlier proposal draft, if present in the handoff. It is useful background but no longer exactly matches the current competition direction. |
| `docs/news_evaluation_usage.md` | Short command reference for news-aware evaluation. |
| `docs/superpowers/specs/2026-07-24-ai-trading-agent-news-evaluation-design.md` | Design note for the news-aware evaluation prototype. |
| `docs/superpowers/plans/2026-07-24-news-aware-trading-evaluation.md` | Implementation plan used for the current prototype. |
| `data/stock_data_1y/` | Local market/fundamental evaluation data, if present. Large data may be local-only. |
| `data/rl_training_data/` | Local PPO training data, if present. |
| `data/news/` | Local raw and normalized news store. |
| `outputs/` | Generated evaluation outputs. This directory is safe to regenerate and should usually not be treated as source code. |
| `tests/` | Unit and regression tests. |

### Scripts

| Path | Purpose |
| --- | --- |
| `scripts/run_evaluation.py` | Runs one or more baseline agents with the configured market data, news store, and evaluation settings. |
| `scripts/collect_news.py` | Collects current-day Finnhub company news into a `NewsStore`. This is intended for live/shadow-live collection. |
| `scripts/backfill_news.py` | Backfills historical Finnhub company news for mechanics testing. Backfill mode is not suitable for official no-leakage leaderboard claims. |
| `scripts/validate_dataset.py` | Checks selected sessions and verifies that emitted `news_seen.jsonl` items do not appear after the decision cutoff. |
| `scripts/train_ppo.py` | Trains a PPO portfolio allocation policy on the public RL training corpus. |
| `scripts/download_stock_data.py` | Downloads or prepares the evaluation stock dataset. |
| `scripts/download_rl_training_data.py` | Downloads or prepares the RL training dataset and derived features. |

### Core package files

| Path | Purpose |
| --- | --- |
| `src/portfolio_agent/__init__.py` | Package marker and high-level package description. |
| `src/portfolio_agent/config.py` | Typed configuration dataclasses, YAML loading, dot-path CLI overrides, and config hashing. |
| `src/portfolio_agent/data_loader.py` | Loads universe files, price data, fundamental statements, aligned trading dates, and RL training data. |
| `src/portfolio_agent/evaluator.py` | Main evaluator implementation. Contains the older walk-forward evaluator and the newer `DailyTradingEvaluator` used for news-aware experiments. |
| `src/portfolio_agent/events.py` | Dataclasses for session clocks, market open events, news events, decision events, and market close events. |
| `src/portfolio_agent/execution.py` | Portfolio state, trade model, close-price target-weight execution, fees, and optional slippage. |
| `src/portfolio_agent/market_calendar.py` | Selects evaluation sessions and constructs regular NYSE-style session clocks. Early-close and holiday details are still simplified. |
| `src/portfolio_agent/metrics.py` | Computes M1-M9 metrics: cumulative return, win rate, Sharpe, Sortino, MDD, VaR, ES, turnover/cost, and violation rate. |
| `src/portfolio_agent/observation.py` | Builds agent-visible observations from market, fundamentals, portfolio state, constraints, and point-in-time news. |
| `src/portfolio_agent/point_in_time.py` | Selects fundamentals available at a cutoff and computes derived ratios and YoY growth. |
| `src/portfolio_agent/risk.py` | Sanitizes target weights and records repairs such as unknown assets, negative weights, cap violations, and excessive gross exposure. |
| `src/portfolio_agent/security.py` | Point-in-time checks and legacy asset-ID helper utilities. The current direction allows tickers/company names but still blocks future data. |

### Agent files

| Path | Purpose |
| --- | --- |
| `src/portfolio_agent/agents/base.py` | Base agent interface with `reset`, `observe`, and `decide` hooks. |
| `src/portfolio_agent/agents/hybrid_rule.py` | Deterministic hybrid baseline using technical, fundamental, and local news signals. |
| `src/portfolio_agent/agents/ppo_portfolio.py` | PPO portfolio policy and news-tilted PPO wrapper. The default untrained/random policy is seeded for reproducibility if no checkpoint is supplied. |
| `src/portfolio_agent/agents/llm_allocation.py` | LLM allocation baseline using the OpenAI-compatible local endpoint and JSON response parsing. |
| `src/portfolio_agent/agents/__init__.py` | Agent package marker. |

### News files

| Path | Purpose |
| --- | --- |
| `src/portfolio_agent/news/models.py` | News dataclasses and stable content hashing. |
| `src/portfolio_agent/news/store.py` | Raw/normalized JSONL news storage, deduplication, ticker-merge logic, visible-news filtering, and file hashes. |
| `src/portfolio_agent/news/normalizer.py` | Provider-specific normalization, currently Finnhub company news. |
| `src/portfolio_agent/news/sentiment.py` | Simple deterministic local keyword-based news sentiment scoring by ticker. |
| `src/portfolio_agent/news/providers/base.py` | Provider protocol. |
| `src/portfolio_agent/news/providers/finnhub.py` | Finnhub company news client. |
| `src/portfolio_agent/news/providers/gdelt.py` | Scaffold for a GDELT supplement provider. |
| `src/portfolio_agent/news/providers/sec_edgar.py` | Scaffold for a SEC EDGAR supplement provider. |
| `src/portfolio_agent/news/providers/__init__.py` | Provider package marker. |

### Tests

| Path | Purpose |
| --- | --- |
| `tests/conftest.py` | Test bootstrap that forces imports from `src/portfolio_agent`. |
| `tests/test_config.py` | Configuration defaults, overrides, and config hashing. |
| `tests/test_daily_evaluator.py` | Daily evaluator behavior, no future news leakage, risk-free-rate propagation, violation accounting, and `event_log.json`. |
| `tests/test_execution.py` | Close-price target-weight execution and fee accounting. |
| `tests/test_market_calendar.py` | Session selection and regular-close session clock behavior. |
| `tests/test_metrics.py` | M1-M9 metric calculations and edge cases. |
| `tests/test_news_providers.py` | Finnhub provider normalization behavior. |
| `tests/test_news_store.py` | News visibility cutoff, deduplication, multi-ticker merge, and sentiment scoring. |
| `tests/test_observation_news.py` | Observation-level news cutoff, latest-news cap, and raw-text hiding. |
| `tests/test_point_in_time.py` | Point-in-time fundamental selection and derived-ratio behavior. |
| `tests/test_reproducibility.py` | Run manifest contents. |
| `tests/test_risk.py` | Target-weight repair and constraint enforcement. |
| `tests/test_scripts_config.py` | Script override parsing and raw news persistence. |
| `tests/test_security_boundary.py` | Legacy asset-ID behavior and current point-in-time safety checks. |

## Setup

Recommended environment:

- Python 3.10 or newer.
- Local access to the market data directory.
- A Finnhub API key if collecting news.
- A local OpenAI-compatible LLM endpoint if running the LLM baseline.

Install the project:

```powershell
python -m pip install -e .
```

Install optional data dependencies if you need to download data:

```powershell
python -m pip install -e ".[data]"
```

Run tests:

```powershell
python -m pytest tests -q
```

## Configuration

The main config file is `configs/evaluation.yaml`.

Important fields:

```yaml
evaluation:
  initial_cash: 1000000
  pre_roll_days: 60
  horizon_trading_days: 10
  seed: 1
  start_date:
  end_date:
  decision_minutes_before_close: 10
  fee_rate: 0.001
  slippage_bps: 0.0
  annualization: 252
  risk_free_rate: 0.0

constraints:
  long_only: true
  max_asset_weight: 0.30
  max_gross_exposure: 1.00

news:
  providers: ["finnhub"]
  data_dir: "data/news"
  max_items_per_decision: 40
  include_raw_text: true
  historical_backfill_mode: false

agents:
  enabled: ["hybrid", "ppo", "llm"]
  llm:
    base_url: "http://localhost:8000/v1"
    api_key: "unused"
    model: "google/gemma-4-31B-it"
```

Any field can be overridden from the command line:

```powershell
--set evaluation.horizon_trading_days=14
--set agents.llm.model=google/gemma-4-31B-it
--set 'agents.enabled=["hybrid","ppo","llm"]'
```

## How to collect news

### Option A: live or shadow-live collection

This is the direction needed for official no-leakage evaluation. The collector
records when the organizer first observed each item, and that timestamp becomes
the availability boundary.

```powershell
$env:FINNHUB_API_KEY="your-finnhub-token"

python scripts/collect_news.py `
  --config configs/evaluation.yaml `
  --data-root data/stock_data_1y `
  --once `
  --set news.data_dir=data/news/live_run
```

For a real competition day, this command should be replaced by a scheduled
collector that polls during the trading day, stores raw payloads immediately,
and never modifies official raw files after the run.

### Option B: historical backfill for feasibility testing

Use this for local mechanics validation only:

```powershell
$env:FINNHUB_API_KEY="your-finnhub-token"

python scripts/backfill_news.py `
  --config configs/evaluation.yaml `
  --data-root data/stock_data_1y `
  --from-date 2026-05-05 `
  --to-date 2026-06-18 `
  --historical-backfill-mode `
  --set news.data_dir=data/news/backfill_20260505_20260618
```

If Finnhub returns `HTTP Error 401: Unauthorized`, the API key is missing,
invalid, expired, or not authorized. Test a single request first:

```powershell
Invoke-RestMethod "https://finnhub.io/api/v1/company-news?symbol=AAPL&from=2026-06-05&to=2026-06-18&token=$env:FINNHUB_API_KEY"
```

If the single request fails with 401, fix the token before rerunning the script.
If it fails with 429, the issue is rate limiting rather than authorization.

## How to run the three baselines

Run all three current baselines over 10 trading days:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/news_eval_10d `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.horizon_trading_days=10 `
  --set 'agents.enabled=["hybrid","ppo","llm"]' `
  --set agents.llm.base_url=http://localhost:8000/v1 `
  --set agents.llm.api_key=unused `
  --set agents.llm.model=google/gemma-4-31B-it
```

Run a 14-trading-day experiment:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/news_eval_14d `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.horizon_trading_days=14 `
  --set 'agents.enabled=["hybrid","ppo","llm"]'
```

Run a fixed date window:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/news_eval_dates `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.start_date=2026-06-05 `
  --set evaluation.end_date=2026-06-18 `
  --set 'agents.enabled=["hybrid","ppo","llm"]'
```

Run without news by pointing to an empty news directory:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/no_news_eval_10d `
  --set news.data_dir=data/news/empty `
  --set evaluation.horizon_trading_days=10 `
  --set 'agents.enabled=["hybrid","ppo","llm"]'
```

Validate an output directory:

```powershell
python scripts/validate_dataset.py `
  --config configs/evaluation.yaml `
  --data-root data/stock_data_1y `
  --output-root outputs/news_eval_10d `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.horizon_trading_days=10
```

## Outputs and logs

Each agent gets its own output directory:

```text
outputs/<run_name>/
  comparison.csv
  comparison.json
  hybrid/
    metrics.json
    daily_nav.csv
    actions.jsonl
    trades.jsonl
    news_seen.jsonl
    violations.jsonl
    run_manifest.json
    event_log.json
  ppo/
    ...
  llm/
    ...
```

File meanings:

| File | Meaning |
| --- | --- |
| `comparison.csv` / `comparison.json` | Cross-agent metric comparison. |
| `metrics.json` | Final M1-M9 metrics for one agent. |
| `daily_nav.csv` | Daily NAV, daily return, cash, transaction cost, and traded notional. |
| `actions.jsonl` | Raw action, sanitized action, violations, and agent diagnostics for each day. |
| `trades.jsonl` | Executed trades, shares before/after, fill price, notional, and fee. |
| `news_seen.jsonl` | News records visible to the agent on each session. |
| `violations.jsonl` | Action repairs, invalid responses, and LLM call/parse failures. |
| `run_manifest.json` | Resolved config, config hash, LLM settings, data hashes, news hashes, code commit, and dirty-state flag. |
| `event_log.json` | Complete per-agent JSON log with observation, news, decision, execution, portfolio state, daily result, running metrics, final metrics, and manifest. |

Inspect the full JSON log:

```powershell
Get-Content outputs/news_eval_10d/llm/event_log.json
```

The `event_log.json` structure is:

```json
{
  "event": "evaluation_run",
  "agent_name": "llm",
  "metrics": {
    "m1_cumulative_return": 0.0123,
    "m2_daily_win_rate": 0.6,
    "m3_sharpe_ratio": 1.1,
    "m4_sortino_ratio": 1.4,
    "m5_maximum_drawdown": 0.03,
    "m6_value_at_risk_95": 0.02,
    "m7_expected_shortfall_95": 0.03,
    "m8_turnover": 1.7,
    "m8_cost_rate": 0.0017,
    "m9_violation_rate": 0.0
  },
  "run_manifest": {},
  "sessions": [
    {
      "event": "daily_session",
      "session_index": 0,
      "session_date": "2026-06-05",
      "clock": {},
      "market_open": {},
      "observation": {},
      "news": [],
      "decision": {},
      "raw_action": {},
      "sanitized_action": {},
      "execution": {},
      "trades": [],
      "portfolio_before": {},
      "portfolio_after": {},
      "daily_record": {},
      "running_metrics": {}
    }
  ]
}
```

## Current agent input/output format

In the current local Python prototype, the evaluator calls:

```python
raw_action = agent.decide(observation)
```

The `observation` is a JSON-serializable Python dictionary. The current action
is a simple dictionary from ticker to target weight:

```json
{
  "AAPL": 0.20,
  "MSFT": 0.15,
  "NVDA": 0.10
}
```

Cash is implicit:

```text
cash_weight = 1.0 - sum(target_weights)
```

Invalid weights are repaired by the evaluator and counted in M9.

### Current observation sample with organizer-provided news

```json
{
  "session_date": "2026-06-05",
  "event_time_utc": "2026-06-05T19:50:00+00:00",
  "assets": [
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "sector": "Technology"
    }
  ],
  "open_prices": {
    "AAPL": 195.00
  },
  "market_features": {
    "AAPL": {
      "relative_price": 1.02,
      "return_1d": 0.004,
      "return_5d": 0.018,
      "return_20d": 0.041,
      "momentum_60d": 0.085,
      "volatility_20d": 0.017,
      "sma_distance_50": 0.026,
      "volume_ratio_20": 1.12,
      "volume_zscore_20": 0.35
    }
  },
  "fundamental_features": {
    "AAPL": {
      "net_margin": 0.24,
      "operating_margin": 0.31,
      "roe": 0.56,
      "fcf_margin": 0.21,
      "debt_to_assets": 0.33,
      "cash_conversion": 1.08,
      "revenue_yoy": 0.07,
      "report_age_days": 34,
      "is_new_report": false,
      "stale_fundamental": false
    }
  },
  "portfolio": {
    "weights": {
      "AAPL": 0.10
    },
    "cash_ratio": 0.90,
    "nav": 1000000.0
  },
  "constraints": {
    "long_only": true,
    "max_asset_weight": 0.30,
    "max_gross_exposure": 1.00,
    "fee_rate": 0.001,
    "slippage_bps": 0.0
  },
  "news": [
    {
      "provider": "finnhub",
      "provider_news_id": "123456",
      "published_at_utc": "2026-06-05T18:15:00+00:00",
      "available_at_utc": "2026-06-05T18:17:03+00:00",
      "ticker": "AAPL",
      "tickers": ["AAPL"],
      "company_names": ["Apple Inc."],
      "headline": "Apple announces a new supplier agreement",
      "summary": "The company announced a new agreement before the close.",
      "source": "ExampleWire",
      "url": "https://example.com/news",
      "content_hash": "stable-story-hash"
    }
  ]
}
```

### Current observation sample without organizer-provided news

```json
{
  "session_date": "2026-06-05",
  "event_time_utc": "2026-06-05T19:50:00+00:00",
  "assets": [
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "sector": "Technology"
    }
  ],
  "open_prices": {
    "AAPL": 195.00
  },
  "market_features": {
    "AAPL": {
      "return_1d": 0.004,
      "return_5d": 0.018,
      "return_20d": 0.041,
      "momentum_60d": 0.085,
      "volatility_20d": 0.017
    }
  },
  "fundamental_features": {
    "AAPL": {
      "net_margin": 0.24,
      "debt_to_assets": 0.33,
      "report_age_days": 34
    }
  },
  "portfolio": {
    "weights": {
      "AAPL": 0.10
    },
    "cash_ratio": 0.90,
    "nav": 1000000.0
  },
  "constraints": {
    "long_only": true,
    "max_asset_weight": 0.30,
    "max_gross_exposure": 1.00,
    "fee_rate": 0.001
  },
  "news": []
}
```

### Recommended future SDK/server JSON format

For the formal platform, use explicit request/response envelopes rather than
the current direct Python dictionary call.

Recommended decision request:

```json
{
  "type": "decision_request",
  "protocol_version": "0.1",
  "run_id": "official_2026_live",
  "team_id": "team_001",
  "session_date": "2026-06-05",
  "deadline_utc": "2026-06-05T19:50:00+00:00",
  "observation": {
    "assets": [],
    "open_prices": {},
    "market_features": {},
    "fundamental_features": {},
    "portfolio": {},
    "constraints": {},
    "news": []
  }
}
```

Recommended decision response:

```json
{
  "type": "decision_response",
  "protocol_version": "0.1",
  "run_id": "official_2026_live",
  "team_id": "team_001",
  "session_date": "2026-06-05",
  "target_weights": {
    "AAPL": 0.20,
    "MSFT": 0.15,
    "NVDA": 0.10
  },
  "metadata": {
    "agent_version": "example-agent-v1",
    "used_external_news": true
  }
}
```

Recommended validation rules:

- The response must arrive before `deadline_utc`.
- `target_weights` must be a JSON object from ticker to numeric target weight.
- Unknown tickers are ignored and counted as violations.
- Negative weights are clipped to zero and counted as violations.
- Per-asset weights above the cap are clipped and counted as violations.
- Gross exposure above 1.0 is scaled down and counted as a violation.
- If the agent fails to respond, the server should either keep the previous
  target weights or move to cash. This policy must be finalized before the
  official competition.
- All raw requests and responses should be logged by the organizer server.

## Metrics

The current metric set is:

| ID | Name | Meaning |
| --- | --- | --- |
| M1 | Cumulative Return | Total portfolio growth over the evaluation period. |
| M2 | Daily Win Rate | Fraction of trading days with positive portfolio return. |
| M3 | Sharpe Ratio | Return per unit of total volatility. |
| M4 | Sortino Ratio | Return per unit of downside volatility. |
| M5 | Maximum Drawdown | Worst peak-to-trough portfolio decline. |
| M6 | Value at Risk | Estimated one-day tail loss at the configured confidence level, currently 95%. |
| M7 | Expected Shortfall | Average loss beyond the VaR threshold. |
| M8 | Turnover and Cost Rate | Total traded notional and transaction fees relative to portfolio value. |
| M9 | Violation Rate | Fraction of decision days on which the action required repair or the agent failed. |

The current code reports both canonical keys such as
`m1_cumulative_return` and short aliases such as `total_return`.

## Baseline agents

### Hybrid rule agent

The hybrid baseline combines:

- momentum and volatility features;
- fundamental ratios such as margins, ROE, leverage, and revenue growth;
- deterministic local news sentiment by ticker.

It is interpretable and useful as the first sanity-check baseline.

### News-tilted PPO agent

The PPO baseline uses a neural portfolio policy. If no checkpoint is provided,
the current implementation uses a seeded policy so that feasibility runs are
reproducible. Since the existing PPO was not trained with news, the current
news-aware behavior is implemented as a deterministic post-policy news tilt.

This is a practical MVP, not a final RL baseline.

### LLM allocation agent

The LLM baseline summarizes the observation into a structured prompt and asks a
local OpenAI-compatible model for JSON target weights. The current default is:

```text
base_url = http://localhost:8000/v1
model = google/gemma-4-31B-it
api_key = unused
```

The evaluator counts LLM call failures and parse failures as violations.

## Suggested feasibility experiments

### Compare different evaluation lengths

Run several horizons and compare stability of metrics:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/news_eval_14d `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.horizon_trading_days=14 `
  --set 'agents.enabled=["hybrid","ppo","llm"]'
```

Suggested sweep:

- 5 trading days: quick smoke test, but metrics are very noisy.
- 10 trading days: approximately two calendar weeks; current default.
- 14 trading days: a useful candidate because it gives more observations while
  staying short enough for a live event.
- 20 trading days: more stable risk metrics, but longer operational burden.

Pay attention to `low_sample_warning` in `metrics.json`. Risk metrics such as
Sharpe, Sortino, VaR, and ES are unstable on very short horizons.

### Compare with-news vs no-news performance

With news:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/with_news_10d `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.horizon_trading_days=10 `
  --set 'agents.enabled=["hybrid","ppo","llm"]'
```

Without news:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/no_news_10d `
  --set news.data_dir=data/news/empty `
  --set evaluation.horizon_trading_days=10 `
  --set 'agents.enabled=["hybrid","ppo","llm"]'
```

Then compare:

```powershell
Get-Content outputs/with_news_10d/comparison.json
Get-Content outputs/no_news_10d/comparison.json
```

This is one of the most important feasibility checks. If news does not change
baseline behavior or only improves results through accidental leakage, the
official news policy should be reconsidered.

### Compare LLM models

Use the same evaluation window, same news store, same seed, and same prompt
format. Only change the LLM model:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/llm_model_test `
  --set news.data_dir=data/news/backfill_20260505_20260618 `
  --set evaluation.horizon_trading_days=10 `
  --set 'agents.enabled=["llm"]' `
  --set agents.llm.base_url=http://localhost:8000/v1 `
  --set agents.llm.model=your/model-name
```

Questions to test:

- Do stronger LLMs produce better portfolio decisions, or just more confident
  but unstable allocations?
- Do LLMs rely on prior knowledge about ticker identities rather than the
  provided observation?
- Does hiding organizer-provided news text change decisions?
- Are results sensitive to the exact prompt wording?

Potential bias experiment:

- Run with real tickers and company names.
- Run a controlled variant that hides names or maps names to neutral aliases.
- Compare allocations and performance. This helps estimate whether the LLM is
  using prior market knowledge rather than only the supplied data.

## Next steps

### 1. Decide a good evaluation horizon

The current default of 10 trading days may be too short for reliable metrics.
Run experiments with:

- `--set evaluation.horizon_trading_days=5`
- `--set evaluation.horizon_trading_days=10`
- `--set evaluation.horizon_trading_days=14`
- `--set evaluation.horizon_trading_days=20`

For each horizon, compare:

- ranking stability across baselines;
- sensitivity of Sharpe/Sortino/VaR/ES;
- frequency of zero-news days;
- operational burden for a live competition;
- whether the horizon is long enough for strategies to recover from one bad
  market day.

### 2. Investigate more public/free news sources

Finnhub is currently the MVP source. More sources should be evaluated before
finalizing the rules.

Candidate sources to investigate:

- Finnhub company news: already implemented, useful for MVP testing.
- SEC EDGAR APIs: free, official, point-in-time friendly, but mostly filings
  and regulatory events rather than general market news.
- GDELT 2.0: free broad web-news coverage, but ticker/company matching needs
  careful validation.
- Alpha Vantage News & Sentiment: convenient format, but free request limits
  may be too restrictive for 30 tickers during a live trading day.
- Alpaca news: useful if entitlement is available, but should not be assumed
  free/open for all participants without checking account terms.
- Other RSS/open web sources: possible, but licensing, deduplication, timestamp
  reliability, and ticker matching must be checked carefully.

For every candidate, record:

- cost and license;
- whether it is public/open enough for participants;
- intraday latency;
- historical availability;
- API rate limits;
- timestamp fields and whether they are trustworthy;
- ticker/company mapping quality;
- whether use is reproducible for top-team code checks.

### 3. Finalize the participant input/output protocol

The current local format is sufficient for feasibility testing, but the formal
SDK/server should have a strict JSON schema.

Open questions:

- Will organizers provide any news in the official observation?
- If not, should the observation still contain `news: []` for schema stability?
- Are participants allowed to call external public APIs during the live window?
- How will the server distinguish allowed public sources from private sources?
- What is the late-submission policy?
- If a team fails to respond, should the server keep previous weights or move
  to cash?
- Should the response support explanations, or only weights?
- Should agents be required to report which external news sources they used?

Recommended near-term action:

- Keep two schemas:
  - `observation_with_news.schema.json`
  - `observation_without_news.schema.json`
- Keep one response schema:
  - `decision_response.schema.json`
- Add schema validation to the future SDK and server.

### 4. Quantify the value of news

Run all baselines with and without news using identical dates and seeds.

Compare:

- cumulative return;
- Sharpe and Sortino;
- MDD, VaR, and ES;
- turnover/cost;
- violation rate;
- decision-level differences in `event_log.json`.

The key question is not only whether news improves return, but whether it
changes behavior in a plausible, auditable way.

### 5. Test different LLMs and possible prior-knowledge bias

Because stock tickers and company names are visible, LLMs may use pretraining
knowledge about companies. This is not automatically wrong if the competition
allows names, but it should be understood.

Experiments to run:

- same observation, different LLMs;
- same LLM, with news vs without news;
- same LLM, with ticker/company names vs neutral aliases;
- same LLM, stronger vs weaker model;
- same LLM, multiple prompts with the same schema.

If prior-knowledge bias is large, the rules should state clearly whether that
is allowed. If the competition wants a more data-driven task, consider a
variant where tickers are visible but external memory-heavy reasoning is
restricted by model choice or prompt constraints.

### 6. Audit unrealistic or fragile parts of the current prototype

Known limitations:

- The market calendar uses regular open/close assumptions and does not fully
  model holidays, early closes, or exchange special hours.
- The execution model uses same-day close execution as a market-on-close
  approximation. This is acceptable for feasibility testing but should be
  reviewed for the official protocol.
- The news collection is not yet a robust scheduled live service.
- SEC EDGAR and GDELT are scaffolded but not production provider integrations.
- There is no participant SDK or remote server yet.
- There is no authentication, rate limiting, or late-response policy.
- There is no official public-source compliance mechanism for participant news.
- PPO is not a fully news-trained RL baseline.
- Very short evaluation windows make risk metrics noisy.
- Generated outputs and local data are not cleanly separated from source code in
  the local working tree.

Recommended additions before building the formal platform:

- JSON schema files for request/response validation.
- A minimal starter-kit agent that connects to a mock server.
- A mock daily server that replays `event_log.json`-compatible observations.
- A live scheduler design for market open, final news poll, decision cutoff,
  execution, and close.
- A data-source policy document explaining allowed public sources and forbidden
  private/proprietary sources.
- A reproducibility-check protocol for the top 5 or top 10 teams.
- A dashboard or report script for comparing multiple runs and horizons.
- A robust immutable audit directory design for official runs.

## Current status

Implemented:

- Configurable evaluation settings and CLI overrides.
- Point-in-time news store with raw and normalized storage.
- Finnhub company news collection and historical backfill.
- News-aware daily evaluator.
- Ticker/company-name visible observations.
- Three news-aware baseline agents: hybrid rule, news-tilted PPO, and LLM.
- M1-M9 metrics.
- Reproducible run manifests with config, data, news, LLM, and code-state
  metadata.
- Full per-agent `event_log.json`.
- Unit/regression tests for the main safety and mechanics checks.

Not implemented yet:

- Official SDK/server platform.
- Participant authentication and submission handling.
- Official live news/data scheduler.
- Full exchange calendar with early closes.
- Finalized official evaluation horizon.
- Finalized allowed-news-source policy.
- Finalized fixed LLM/model policy.
- Starter kit for external participants.

## Handoff checklist

Before passing the project to another maintainer, run:

```powershell
python -m pytest tests -q
```

Run a short smoke test:

```powershell
python scripts/run_evaluation.py `
  --data-root data/stock_data_1y `
  --config configs/evaluation.yaml `
  --output-root outputs/handoff_smoke `
  --set evaluation.horizon_trading_days=2 `
  --set 'agents.enabled=["hybrid","ppo"]'
```

Confirm that this file exists:

```powershell
Test-Path outputs/handoff_smoke/hybrid/event_log.json
```

Then review:

```powershell
Get-Content outputs/handoff_smoke/hybrid/metrics.json
Get-Content outputs/handoff_smoke/hybrid/event_log.json
```

The next maintainer should treat this repository as a feasibility research
prototype. The immediate goal is to identify protocol holes, compare design
choices, and prepare enough evidence to design the real competition platform
and starter kit later.
