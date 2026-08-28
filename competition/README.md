# ACM ICAIF 2026 Trading Agent Competition - platform

Three top-level directories:

| Dir | Owner | What it is |
| --- | --- | --- |
| `data/`  | organizer | official price + news data, published daily |
| `code/`  | organizer | the framework given to everyone: agent API, local backtester, panel builder, execution/scoring engine, ingestion |
| `alpha/` | **participant** | the team's strategy - the one thing they write and submit |

Read `RULES.md` for the full rules and `HANDOVER.md` for the deployment guide.

## How participation works

Teams develop and **backtest locally** using `code/` + `data/`, writing their strategy in
`alpha/agent.py`. On each live day they run their agent locally and **submit only the target
weights** to the website. The website is a thin service: it publishes data, records
submissions, keeps the history, and computes execution + the leaderboard. **It never runs
participant code.** (Top teams' code is re-run once, post-competition, for a reproducibility
audit.)

## Participant: backtest your alpha (self-service)

```bash
python competition/code/backtest.py --alpha competition/alpha \
    --data-root data/stock_data_1y --start 2026-06-05 --end 2026-06-18 --check-lookahead
```

Runs your alpha under the official rules (next-open execution, 0.1% fee, caps) and prints
M1-M9 - the same engine that scores the leaderboard. `--check-lookahead` verifies your alpha
does not use future data (see `examples/lookahead_cheater` for a caught case).

## Organizer: run a trading day (demo replay)

```bash
PY="python"; NEWS=competition/data/news/official
# 1. ingest public news for the universe
$PY competition/code/daily_cycle.py ingest-news --data-root data/stock_data_1y \
    --start 2026-06-05 --end 2026-06-18 --news-dir $NEWS
# 2. publish the daily observation panels from fresh data
$PY competition/code/daily_cycle.py publish --data-root data/stock_data_1y \
    --start 2026-06-05 --end 2026-06-18 --news-dir $NEWS
# 3. (team, own machine) run alpha and submit weights
$PY competition/code/submit.py --requests-dir competition/requests \
    --code competition/alpha --team-id team_001 --posts-dir competition/posts
# 4. organizer: settle submitted weights (never runs team code) + leaderboard
$PY competition/code/daily_cycle.py run --data-root data/stock_data_1y \
    --start 2026-06-05 --end 2026-06-18 --news-dir $NEWS \
    --team team_001 --source posted --posted-dir competition/posts
$PY competition/code/daily_cycle.py board
```

(Requests are produced by the publish/run step. `--source code` is the post-competition
reproducibility-audit / validation mode; `--source posted` is the live mode.)

## Tests

```bash
pytest competition/tests ../tests -q
```
