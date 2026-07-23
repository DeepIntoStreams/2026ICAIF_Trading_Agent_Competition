# News Evaluation Usage

Run a default 10-session replay:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval
```

Run a 5-session experiment:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval_5d --set evaluation.horizon_trading_days=5
```

Change the LLM model:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval_llm --set 'agents.enabled=["llm"]' --set agents.llm.base_url=http://localhost:8000/v1 --set agents.llm.model=google/gemma-4-31B-it
```

Use explicit dates:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval_dates --set evaluation.start_date=2026-06-05 --set evaluation.end_date=2026-06-18
```

Run a non-LLM smoke test:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/final_smoke --set evaluation.horizon_trading_days=5 --set 'agents.enabled=["hybrid","ppo"]'
```

Validate an output directory:

```powershell
python scripts/validate_dataset.py --config configs/evaluation.yaml --data-root data/stock_data_1y --output-root outputs/final_smoke --set evaluation.horizon_trading_days=5
```

Inspect the complete per-agent JSON event log:

```powershell
Get-Content outputs/news_eval_10d/llm/event_log.json
```

Collect Finnhub company news for the current UTC date:

```powershell
$env:FINNHUB_API_KEY="your-token"
python scripts/collect_news.py --config configs/evaluation.yaml --data-root data/stock_data_1y --once
```

Backfill Finnhub company news for mechanics validation:

```powershell
$env:FINNHUB_API_KEY="your-token"
python scripts/backfill_news.py --config configs/evaluation.yaml --data-root data/stock_data_1y --from-date 2026-06-05 --to-date 2026-06-18 --historical-backfill-mode
```

Important notes:

- `historical_backfill_mode=true` is for mechanics validation, not official leaderboard claims.
- Official no-leakage runs should use shadow-live collected news with real `first_seen_at_utc` timestamps.
- All CLI `--set` overrides are recorded in each agent's `run_manifest.json`.
