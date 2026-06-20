# trading_competition_baseline
> Walk-Forward Portfolio Agent Evaluation System

A reproducible, confidentiality-enforced evaluation framework for
portfolio-allocation agents. On each trading step the evaluator supplies a
strictly past-only, anonymized observation and receives target portfolio
weights. Three baseline agents — rule-based, reinforcement-learning, and
LLM-based — are evaluated under the same market, accounting, and security
rules.

---

## Table of Contents

1. [Overview](#overview)
2. [Security and Confidentiality](#security-and-confidentiality)
3. [Data Specification](#data-specification)
4. [Evaluation Mechanism](#evaluation-mechanism)
5. [Baseline Agents](#baseline-agents)
6. [Evaluation Metrics](#evaluation-metrics)
7. [Project Structure](#project-structure)
8. [Setup and Installation](#setup-and-installation)
9. [How to Run](#how-to-run)
10. [Audit Trail](#audit-trail)

---

## Overview

The system separates **private evaluator state** from **agent-visible state**.
Agent code is treated as potentially curious: it may inspect every field it
receives, attempt to infer asset identities, or call external services. The
evaluator therefore minimizes information before calling any agent and
restricts the execution environment.

Key design decisions:

- The entire one-year evaluation interval remains hidden and out-of-sample.
- The RL policy is trained only on a separate public historical corpus and is
  frozen before the private universe is selected.
- Raw identities, dates, prices, volumes, and financial statement amounts
  never cross the evaluator-to-agent boundary.
- Observations use HMAC-derived opaque asset IDs, relative price features,
  and fundamental ratios only.

---

## Security and Confidentiality

### Protected Information

| Protected item          | Why it is sensitive                    | Agent-safe replacement              |
|-------------------------|----------------------------------------|--------------------------------------|
| Ticker / company name   | Reveals the selected universe          | Opaque HMAC-derived asset ID         |
| Absolute calendar date  | Reveals the hidden evaluation interval | `step_id` and relative report age    |
| Raw OHLC price          | Can fingerprint a listed security      | Relative price and rolling returns   |
| Raw volume              | Can fingerprint a listed security      | Rolling volume z-score and ratio     |
| Raw financial amounts   | Revenue/assets can identify a company  | Margins, growth, leverage ratios     |
| Share count             | Combined with weight can reveal price  | Portfolio weight only                |

### Security Boundary

```
PRIVATE EVALUATOR
  ticker, calendar date, raw OHLCV, filings, asset mapping, execution ledger
        |
        | deterministic minimization + point-in-time filter + anonymization
        v
AGENT-SAFE OBSERVATION
  asset_id, step_id, relative market features, fundamental ratios, weights
        |
        v
AGENT ACTION: target_weights only
```

### Training-Time Isolation

- Training jobs read only the public development-data root
  (`rl_training_data/`); the secure evaluation root (`stock_data_1y/`) is
  never mounted into the process.
- The 60 training symbols and the 30 evaluation symbols are completely
  disjoint with zero overlap.
- An embargo of 120 calendar days separates the training data end from the
  evaluation start.
- Feature scalers are fitted only on public training data; no scaler is
  fitted on the hidden year.

---

## Data Specification

### Evaluation Universe (`stock_data_1y/`)

30 U.S. listed equities across six sector groups, each contributing five
assets:

| Sector group              | Tickers                              |
|---------------------------|--------------------------------------|
| Technology                | AAPL, MSFT, NVDA, INTC, CRM         |
| Finance                   | JPM, BAC, GS, V, PYPL               |
| Healthcare                | LLY, JNJ, UNH, PFE, TMO             |
| Consumer                  | AMZN, TSLA, WMT, NKE, KO            |
| Industrial & Energy       | CAT, GE, BA, XOM, CVX               |
| Communication & Utilities | GOOGL, META, DIS, T, NEE            |

**Price data** — `prices_daily/{TICKER}.csv`:
Daily OHLCV with adjusted close. Approximately 252 trading days.

| Column     | Description                         |
|------------|-------------------------------------|
| Date       | Trading date                        |
| open/high/low/close | Unadjusted OHLC              |
| adj_close  | Split- and dividend-adjusted close  |
| volume     | Daily traded shares                 |

**Fundamental data** — `fundamentals_quarterly/{TICKER}/`:
Three files per ticker (`income_statement.csv`, `balance_sheet.csv`,
`cash_flow.csv`) with quarterly financial data. Since `available_at`
timestamps are not provided by the data source, the system estimates
`available_at = period_end + 45 calendar days`.

### RL Training Corpus (`rl_training_data/`)

60 non-evaluation equities, approximately three years of daily OHLCV with
pre-computed features.

| File             | Rows     | Description                                |
|------------------|----------|--------------------------------------------|
| `train.csv`      | ~29,940  | Training data (before 2024-02-16)          |
| `validation.csv` | ~15,120  | Validation data (2024-02-16 onwards)       |
| `metadata.json`  | —        | Download dates, embargo info, symbol list  |

Pre-computed feature columns: `return_1d`, `return_5d`, `return_20d`,
`momentum_60d`, `volatility_20d`, `sma_50`, `sma_distance_50`,
`volume_ma_20`, `volume_ratio_20`.

### Data Partition Summary

| Partition               | Visibility           | Purpose                        | In metrics? |
|-------------------------|----------------------|--------------------------------|-------------|
| Public training corpus  | Visible to developer | RL training and validation     | No          |
| 60-day private pre-roll | Sequential by eval   | Initialize rolling features    | No          |
| One-year private eval   | One step at a time   | Out-of-sample evaluation       | Yes         |

---

## Evaluation Mechanism

### Fixed Portfolio Rules

| Item               | Setting                                             |
|--------------------|-----------------------------------------------------|
| Initial capital    | 1,000,000                                           |
| Position direction | Long-only                                           |
| Per-asset limit    | 30% of portfolio NAV                                |
| Gross exposure     | At most 100%; the residual remains cash             |
| Transaction fee    | 0.1% of executed notional on both buys and sells    |
| Decision frequency | One action per trading day                          |
| Execution timing   | Observation after close *t*; execution at open *t+1*|
| Share model        | Fractional shares                                   |

### Walk-Forward Step Protocol (Daily Event Order)

1. **Open *t***: Execute the target weights submitted after close *t−1*.
2. Validate and repair the action (see action sanitization below); sell
   before buying; charge 0.1% on executed notional.
3. **Close *t***: Mark holdings to adjusted close; update cash, NAV, returns,
   and drawdown.
4. Build an agent-safe observation using close *t* as the information cutoff.
5. Call the agent; retain the sanitized target weights for open *t+1*.

### Agent-Safe Observation Format

```json
{
  "step_id": 81,
  "market_features": {
    "asset_8c31e4a7": {
      "relative_price": 1.015,
      "return_1d": 0.0102,
      "return_5d": 0.032,
      "return_20d": 0.058,
      "momentum_60d": 0.12,
      "sma_distance_50": 0.031,
      "volatility_20d": 0.018,
      "volume_ratio_20": 1.2,
      "volume_zscore_20": 0.42
    }
  },
  "fundamental_features": {
    "asset_8c31e4a7": {
      "net_margin": 0.21,
      "operating_margin": 0.28,
      "roe": 0.35,
      "fcf_margin": 0.18,
      "debt_to_assets": 0.28,
      "cash_conversion": 1.05,
      "revenue_yoy": 0.07,
      "report_age_days": 34,
      "is_new_report": false,
      "stale_fundamental": false
    }
  },
  "portfolio": {
    "weights": {"asset_8c31e4a7": 0.16},
    "cash_ratio": 0.35,
    "nav_ratio": 1.015,
    "drawdown": -0.012
  },
  "constraints": {
    "long_only": true,
    "max_asset_weight": 0.30,
    "max_gross_exposure": 1.00,
    "fee_rate": 0.001
  }
}
```

### Agent Action Format

```json
{
  "target_weights": {
    "asset_8c31e4a7": 0.18,
    "asset_d4a52f10": 0.20
  }
}
```

Cash is implicit: `cash_weight = 1 − sum(target_weights)`.

### Action Sanitization

| Condition                        | Repair                    | Recorded violation |
|----------------------------------|---------------------------|--------------------|
| Unknown asset ID                 | Remove entry              | `unknown_asset`    |
| NaN, infinity, or non-numeric    | Set weight to zero        | `invalid_number`   |
| Negative weight                  | Clip to zero              | `short_position`   |
| Weight above 0.30               | Clip to 0.30              | `asset_cap`        |
| Weight sum above 1.00           | Scale proportionally      | `gross_exposure`   |
| Agent error or missing action    | Retain previous target    | `invalid_action`   |

Both raw and sanitized actions are recorded in the private audit log.
Repair allows the episode to continue while the original error still
contributes to `violation_rate`.

### Execution Model

```
NAV_open(t) = cash(t−) + Σᵢ shares_i(t−) × open_i(t)
target_value_i = target_weight_i × NAV_open(t)
target_shares_i = target_value_i / open_i(t)
trade_value_i = |target_shares_i − old_shares_i| × open_i(t)
fee_i = trade_value_i × 0.001
NAV_close(t) = cash(t) + Σᵢ shares_i(t) × close_i(t)
```

Sells execute before buys. If fees make intended purchases unaffordable, all
buy orders are scaled by a common factor so cash does not fall below zero.

---

## Baseline Agents

### HybridRuleAgent

A deterministic, interpretable baseline combining technical and fundamental
signals.

**Scoring formula:**
```
technical_i = [0.6 × momentum20_i + 0.4 × momentum60_i] / max(vol20_i, ε)
fundamental_i = 0.25·z(revenue_yoy) + 0.25·z(fcf_margin)
              + 0.20·z(net_margin) + 0.15·z(roe) − 0.15·z(debt_to_assets)
final_score_i = 0.70·z(technical) + 0.30·z(fundamental)
```

**Rebalancing**: Every 5 trading days. Keeps assets with positive final score
and price above the 50-day moving average; selects at most 8 positions;
assigns weights proportional to score/volatility; projects to the long-only
capped simplex. If no asset qualifies, the portfolio remains in cash.

### PPOPortfolioAgent

A learned cross-asset allocation policy trained with Proximal Policy
Optimization on the public training corpus.

**Architecture:**
```
features[N, F] → shared MLP → embeddings[N, D]
embeddings → self-attention → attended[N, D]
attended → linear → asset_logits[N]
concat(asset_logits, cash_logit) → softmax → cap projection → weights
```

- Shared parameters make the policy independent of ticker names and asset
  ordering.
- Training uses randomly sampled public episodes (30 assets × 126 days).
- Reward: `log(NAV_t / NAV_{t−1}) − λ_dd·max(0, |drawdown| − 0.10) − λ_v·violation`.
- Model weights are frozen before private evaluation. No gradient update or
  online fine-tuning is permitted during evaluation.

**Per-asset feature vector (13 dimensions):**
Market (7): `return_1d`, `return_5d`, `return_20d`, `momentum_60d`,
`volatility_20d`, `sma_distance_50`, `volume_ratio_20`.
Fundamental (4): `net_margin`, `fcf_margin`, `debt_to_assets`, `roe` (zeros
during training). Portfolio (1): current weight. Freshness (1):
`report_age_days` (zero during training).

### LLMAllocationAgent

A constrained reasoning baseline that uses a local LLM for allocation
decisions.

**Pipeline:**
1. A deterministic summarizer converts the observation into one compact text
   line per asset: momentum, trend, volatility, liquidity, profitability,
   growth, leverage, report age, current weight, and candidate rank.
2. The LLM receives no raw price table, no ticker, no date, no financial
   amount.
3. Called every 5 trading days with temperature 0; must return schema-valid
   JSON.
4. Robust JSON parsing handles truncated responses, markdown-wrapped output,
   and alternative response schemas.

**LLM endpoint:** `http://10.86.229.182:8000/v1` (OpenAI-compatible, local
model `google/gemma-4-31B-it`).

---

## Evaluation Metrics

| Metric               | Definition                                                | Direction     |
|----------------------|-----------------------------------------------------------|---------------|
| Total return         | NAV_T / NAV_0 − 1                                        | Higher        |
| Annualized return    | (NAV_T / NAV_0)^(252/N) − 1                              | Higher        |
| Annualized volatility| √252 × std(r_t)                                          | Lower (equal return) |
| Sharpe ratio         | √252 × mean(r_t) / std(r_t), risk-free = 0               | Higher        |
| Sortino ratio        | √252 × mean(r_t) / downside_std(r_t)                     | Higher        |
| Maximum drawdown     | min_t [NAV_t / running_max_t − 1]                         | Magnitude lower |
| Calmar ratio         | annualized_return / |max_drawdown|                        | Higher        |
| Turnover             | Σ(executed notional) / average NAV                        | Lower (equal return) |
| Cost rate            | Σ(transaction fees) / initial capital                     | Lower         |
| Violation rate       | decision steps with violations / all decision steps       | Lower         |

The 60-day pre-roll interval is excluded from every metric. If return
volatility is zero, Sharpe and Sortino are defined as zero. If maximum
drawdown is zero, Calmar is reported as N/A. The primary comparison order is
**Sharpe → total return → max drawdown → cost rate → violation rate**.

---

## Project Structure

```
portfolio_agent_project/
├── README.md
├── pyproject.toml
├── configs/
│   └── evaluation.yaml          # Evaluation parameters and constraints
├── docs/
│   ├── Portfolio_Agent_Project_Technical_Report_EN.docx
│   └── audit_log_example.jsonl  # Sample audit log format
├── models/                      # PPO checkpoints (generated by training)
│   └── ppo_checkpoint.pt
├── results/                     # Sanitized evaluation outputs (agent-safe)
│   ├── comparison.json
│   ├── hybrid_rule/
│   │   ├── metrics.json
│   │   ├── daily_nav.csv
│   │   └── violations.jsonl
│   ├── ppo/
│   └── llm/
├── private_audit/               # Private audit logs (evaluator-only, NEVER shared)
│   ├── hybrid_rule/
│   │   └── audit_YYYYMMDD_HHMMSS.jsonl
│   ├── ppo/
│   └── llm/
├── scripts/
│   ├── download_stock_data.py          # Download 30 evaluation equities
│   ├── download_rl_training_data.py    # Download 60 training equities
│   ├── train_ppo.py                    # PPO training on public corpus
│   └── run_evaluation.py              # Main evaluation runner
├── src/portfolio_agent/
│   ├── __init__.py
│   ├── security.py              # HMAC asset IDs, forbidden-field scanner
│   ├── point_in_time.py         # Point-in-time fundamental selection, YoY growth
│   ├── observation.py           # Anonymized observation builder
│   ├── risk.py                  # Target-weight validation and repair
│   ├── metrics.py               # Sharpe, Sortino, Calmar, drawdown, etc.
│   ├── data_loader.py           # Load evaluation and training data
│   ├── evaluator.py             # Walk-forward evaluator engine + audit log
│   └── agents/
│       ├── __init__.py
│       ├── base.py              # Abstract agent interface
│       ├── hybrid_rule.py       # Rule-based technical + fundamental agent
│       ├── ppo_portfolio.py     # PPO-trained neural network agent
│       └── llm_allocation.py    # LLM-based allocation agent
└── tests/
    ├── test_security_boundary.py
    ├── test_point_in_time.py
    ├── test_risk.py
    └── test_metrics.py
```

---

## Setup and Installation

### Prerequisites

- Python >= 3.10
- CUDA-capable GPU (recommended for PPO training; CPU also works)

### Install

```bash
cd portfolio_agent_project
pip install -e .
```

For downloading fresh data (optional):

```bash
pip install -e ".[data]"
```

### Verify

```bash
# On Linux/macOS
PYTHONPATH=src python -m unittest discover -s tests -v

# On Windows PowerShell
$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v
```

---

## How to Run

### Step 1: Train the PPO Agent

Train on the public RL corpus only. No evaluation data is loaded.

```bash
python scripts/train_ppo.py \
    --data_root rl_training_data \
    --output models/ppo_checkpoint.pt \
    --num_iterations 200 \
    --episodes_per_iter 8 \
    --seed 42
```

### Step 2: Run Evaluation

Evaluate one or more agents against the private evaluation data.

```bash
python scripts/run_evaluation.py \
    --data_root stock_data_1y \
    --config configs/evaluation.yaml \
    --agents hybrid_rule ppo llm \
    --output_dir results \
    --audit_dir private_audit
```

To evaluate a single agent:

```bash
python scripts/run_evaluation.py \
    --data_root stock_data_1y \
    --agents hybrid_rule \
    --output_dir results \
    --audit_dir private_audit
```

### Output Directories

| Directory       | Contents                                           | Shareable? |
|-----------------|----------------------------------------------------|------------|
| `results/`      | Metrics, daily NAV, violations (anonymous IDs only) | Yes        |
| `private_audit/`| Raw tickers, prices, trades, full execution ledger  | **No**     |

---

## Audit Trail

The evaluator writes a detailed JSONL audit log to `private_audit/` that
records every interaction between the evaluator and the agent. This log
contains raw private data (tickers, dates, prices, share counts) and must
**never** be shared with agents or included in agent-accessible storage.

Each line is a self-contained JSON object with an `event` and `phase` field:

| Event / Phase             | Contents                                                  |
|---------------------------|-----------------------------------------------------------|
| `evaluation_start`        | Config, ticker list, asset-ID mapping, sector mapping     |
| `step / execution`        | Per-trade detail: ticker, direction, shares, price, fee   |
| `step / mark_close`       | NAV, cash, per-ticker holdings with close prices          |
| `step / observation_sent` | Count of assets with market/fundamental features          |
| `step / agent_action`     | Raw action, sanitized action, violations, position count  |
| `evaluation_end`          | Final metrics, total costs, violation summary             |

See `docs/audit_log_example.jsonl` for a formatted sample.
