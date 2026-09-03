# Feasibility study - ICAIF 2026 Trading Agent Competition

Consolidated record of the **feasibility phase** (README next-steps #1-#6): experiment results,
what was built, and the deeper analysis. Merges the three former summaries (`RESULTS_SUMMARY.md`,
`IMPLEMENTATION_SUMMARY_2_3_6.md`, `SUMMARY_NEXTSTEPS_2_3_6.md`).

> Archived record. Some specifics here (repair-not-reject validation, same-day-close execution,
> feature-based observations, the earlier cap) were **superseded** by the current live design
> (reject-not-repair, 9:00 AM ET cutoff / 9:30 open fill, raw-OHLCV + raw-fundamentals
> observations). Kept for the reasoning and experiments behind those decisions.

## Contents
- **Part 1 - Results** (#1 horizon | #4 value of news | #5 LLM identity bias | 6-month backtest)
- **Part 2 - Implementation** (#2 news sources | #3 I/O protocol | #6 audit) - what was built + verify
- **Part 3 - Deeper analysis** (#2 | #3 | #6) - recommendations at execution level

---

## Part 1 - Results (#1, #4, #5 + 6-month backtest)


(Numbering follows the README "Next steps" list: #1 evaluation horizon, #4 value of news,
#5 different LLMs / prior-knowledge bias. Steps #2, #3, #6 are covered in a separate summary.)

**Setup.** Eval universe = 30 tickers (6 sectors). Price data 2025-06-20 -> 2026-06-18.
News store = `data/news/backfill_20260605_20260618` (covers 2026-05-05 -> 06-18 only,
~6 weeks). Seed 1, LLM = `google/gemma-4-31B-it` @ temp 0.0 (deterministic, single seed).
PPO = policy trained on the RL corpus (`models/ppo_checkpoint.pt`, best val_reward
+0.0628 at iter 100); news enters PPO only as a deterministic post-policy tilt.

Two facts to keep in mind while reading: the 6-month runs are **news-off** (news covers
only the last ~6 weeks of that window), and the LLM is **single-seed / deterministic**
(one draw, not an expectation).

---

### Headline: 6-month backtest (126 trading days, 2025-12-17 -> 2026-06-18, news-off)

Real sample (n=126, **no** low-sample warning). Benchmark = naive equal-weight buy-&-hold.

| Agent | Cum. return | Sharpe | MDD | Turnover |
|---|---|---|---|---|
| Rule-based (hybrid) | +6.19% | 0.96 | 6.51% | 32.2 |
| PPO (trained) | **+10.27%** | **1.63** | 8.32% | **6.9** |
| LLM | **+12.51%** | 1.15 | 10.85% | 34.6 |
| **Buy & hold (equal-weight)** | **+13.52%** | - | - | ~0 |

**The key finding: no strategy beat naive equal-weight buy-and-hold (+13.52%).** The LLM
came closest (+12.5%) but with the worst drawdown and highest turnover. PPO is the most
*efficient* - 82% of the LLM's return at 1/5 the turnover and the best Sharpe.

#### Why the benchmark is +13.52% - read the distribution, not the mean

| | Value |
|---|---|
| Equal-weight **mean** | +13.52% |
| **Median** asset | +6.88% |
| Best | INTC **+271.68%** |
| Worst | CRM -40.81% |
| Positive | 20 / 30 assets |

The mean is inflated almost entirely by **INTC +272%**. Drop that one name and the other
29 average ~ +4.6%. So the *typical* asset made ~6.9%, which the rule-based agent roughly
matched and PPO/LLM beat. The whole "who wins" story is sensitive to one outlier stock.

---

### Exp #1 - Decide a good evaluation horizon (horizon sweep, cumulative return %, with news)

Nested windows, all ending 2026-06-18 (h=20 contains h=14 contains h=10 contains h=5).
`ret%` = total over the window; Sharpe/Sortino in the source files are annualized (sqrt252),
so do **not** compare the two on short horizons.

| Trading days | Window | hybrid | ppo | llm |
|---|---|---|---|---|
| 5 | 06-12 -> 06-18 | +0.83 | -0.09 | +0.99 |
| 10 | 06-05 -> 06-18 | +1.47 | +1.27 | +1.69 |
| 14 | 06-01 -> 06-18 | -0.17 | -0.55 | -0.47 |
| 20 | 05-21 -> 06-18 | -1.12 | -0.00 | -3.91 |

**Ranking is unstable.** LLM leads at 5/10 days, but at 20 days it is worst (-3.91) and
PPO is best (~0). Returns flip sign at h=14 because the added earlier days were a down
stretch. The winner at any single horizon is decided by which few days you happen to include.

**Suggestion: don't pick one horizon - average across horizons 5-20.** Rather than commit
to a single evaluation length and inherit its luck, report each agent's metrics *averaged
over the 5/10/14/20-day settings*, which smooths out the window-specific noise:

| Agent | Mean ret% (5-20) | Stdev across horizons | Mean Sharpe |
|---|---|---|---|
| hybrid | +0.25 | 0.98 | 2.01 |
| ppo | +0.16 | 0.67 | 0.42 |
| llm | -0.43 | 2.16 | 1.55 |

The averaged view tells a steadier story than any single row: hybrid and PPO are roughly
flat-but-stable (low stdev), while the LLM has the highest across-horizon volatility (2.16)
- i.e. its edge is the least robust to how long you evaluate. We'd suggest the competition
adopt a **multi-horizon average** (and report the stdev as a stability score) rather than a
single fixed horizon. Methodological note: these four windows are *nested* (all end
2026-06-18), so the average is a robustness summary over evaluation lengths, not four
independent periods; a stricter version would use non-overlapping windows.

---

### Exp #4 - Quantify the value of news, 10-day window (2026-06-05 -> 06-18)

| Agent | Metric | With news | No news | delta |
|---|---|---|---|---|
| hybrid | ret% | 1.47 | 1.28 | +0.19 |
| hybrid | Sharpe | 3.57 | 2.54 | +1.03 |
| hybrid | MDD% | 1.15 | 1.97 | -0.82 (better) |
| hybrid | turnover | 5.51 | 3.11 | +2.40 |
| ppo | ret% | 1.27 | 1.32 | -0.05 |
| ppo | Sharpe | 2.54 | 2.58 | -0.04 |
| ppo | MDD% | 1.20 | 1.24 | -0.04 (better) |
| ppo | turnover | 2.09 | 1.34 | +0.75 |
| llm | ret% | 1.69 | 1.78 | -0.09 |
| llm | Sharpe | 2.56 | 2.79 | -0.23 |
| llm | MDD% | 2.47 | 2.14 | +0.33 (worse) |
| llm | turnover | 3.35 | 2.96 | +0.39 |

At 10 days: news helps **hybrid** (by design, via its 0.20 sentiment weight, at 2x turnover),
is ~neutral for **PPO** (news-blind policy), and slightly *hurts* the **LLM**.

### Exp #4b - Value of news, full news-covered window (2026-05-05 -> 06-18, 32 days)

Biggest clean news sample available (n=32, **no** low-sample warning).

| Agent | With news | No news | delta (news effect) |
|---|---|---|---|
| hybrid | +0.85% | +0.35% | +0.49 |
| ppo | +1.31% | +1.56% | -0.25 |
| llm | -1.01% | -7.27% | **+6.26** |

**The news effect on the LLM flips sign between windows** - slightly negative at 10 days,
strongly positive here (+6.3pp, cutting a -7.3% loss to -1.0%). This instability is the
real finding: one window is not evidence. Diagnosis of *why* the LLM often fails to gain
from news (from inspecting `news_seen.jsonl`): the feed is ~80% Yahoo aggregator headlines,
many market-wide ("Which S&P 500 stocks are moving Friday?"), and ticker mapping is
unreliable (a Northrop headline tagged BA; a SpaceX headline tagged T; an Ackman-on-MSFT
headline tagged CRM). Low signal + mis-tagging + strong model priors -> news rarely moves
the LLM in a short window. Points straight at README step #2 (better sources, ticker
mapping).

---

### Exp #5 - Test different LLMs / prior-knowledge bias (identity ablation: real vs neutral aliases), 10 days

Identical window/seed/prompt/numbers; only asset identity differs (AAPL/"Apple Inc." vs
ASSET_07/"Company 07"). News off in both arms. `scripts/exp3_identity_ablation.py`.

| Metric | Real | Alias | delta |
|---|---|---|---|
| Cumulative return % | 1.78 | 1.74 | -0.04 |
| Daily win rate % | 60.0 | 60.0 | 0.00 |
| Sharpe | 2.79 | 2.84 | +0.05 |
| Sortino | 3.27 | 3.19 | -0.08 |
| Max drawdown % | 2.14 | 2.24 | +0.10 |
| VaR95 % | 1.12 | 1.15 | +0.03 |
| ES95 % | 1.49 | 1.35 | -0.14 |
| **Turnover** | **2.96** | **3.80** | **+0.84** |
| Violation rate % | 10.0 | 10.0 | 0.00 |

Decision-level divergence (from diffing `actions.jsonl`, not in the metrics files):
- Mean daily allocation L1 difference = **0.355** (~35% of the portfolio reshuffled).
- **0 of 10 days** produced identical holdings.
- Turnover +28% under aliasing.

**Interpretation.** Every *outcome* metric is nearly identical, but the *decisions* are not:
hiding identity reshuffles ~35% of the book and trades 28% more, yet lands on the same
return. So the LLM **does** trade on company identity (prior knowledge), and in this
window that prior was roughly performance-neutral. This is the publishable nuance - the
competition can allow names, but should report an identity-ablation number, because the
effect is large on decisions even when small on returns, and another market window could
make it matter (cf. the 6-month LLM edge coming largely from avoiding known losers).


---

## Part 2 - Implementation: #2 news, #3 protocol, #6 audit (what was built)


What was broken, what was built to fix it, and the exact command to verify each fix. This is
the "what I did" companion to `SUMMARY_NEXTSTEPS_2_3_6.md` (the deeper analysis). Everything
here is runnable from a clean clone.

### 30-second verification

```bash
pip install -e . && pip install jsonschema pytest

# #3 + #6: all fixes covered by tests
pytest tests -q                        # -> 19 passed

# #2: real news provider, live (free, no API key; use your own email)
python scripts/survey_news_sources.py --source sec_edgar \
    --tickers AAPL MSFT NVDA JPM --from 2025-06-01 --to 2025-12-31 \
    --user-agent "you you@example.com"

# #3: full competition loop through the request/response envelopes
python scripts/mock_server.py --event-log outputs/exp2_with_news_10d/llm/event_log.json
```

---

### #2 - More public/free news sources

**Why this matters (the timestamp problem).** The competition's core rule: an agent deciding
at, say, 3:50 PM may only see news that was already public before 3:50 PM. Each news item is
allowed in or filtered out based on one timestamp - when it became public. So that timestamp
has to be *honest*. If an item can claim "published 3:45 PM" when it actually appeared at
4:30 PM, future news leaks into the decision and every result is worthless.

**The issue with the current source.** Finnhub was the only working provider (the other two
files were empty stubs that returned nothing). Worse, in the Finnhub data the "when it became
public" time is just the **publisher's own claim** - I checked, and for all 5,727 items it is
identical to the raw publish date, with no independent record of when it was really first
seen. A publisher's claim can be wrong or backdated, so this source cannot prove no-leakage.
(Its feed is also ~80% aggregator noise with tickers tagged to the wrong company - see
`RESULTS_SUMMARY.md`, Exp #4b.)

**What I did.**
- **Added a source with trustworthy timestamps: SEC EDGAR** (`providers/sec_edgar.py`, real
  and live-tested). Company filings (8-K, 10-Q, 10-K, etc.) come with the **exact second the
  U.S. government received and posted them**. Nobody can fake or backdate that - the SEC sets
  it, not the company. So every item has a timestamp you can actually trust, which is exactly
  what the no-leakage rule needs. Free, no API key.
- **Added a second free source, GDELT** (`providers/gdelt.py`) - broad web news, listed as a
  candidate in the README. Built to their published API, but GDELT's servers were blocked from
  the machine I worked on, so I could **not run it live** - it needs a quick confirmation run
  from a normal network. Kept because the README explicitly lists it to investigate.
- **A survey tool** (`scripts/survey_news_sources.py`) that *runs* a source and prints plain
  facts, so nobody has to take my word for it: how many items it returned, how many of your
  stocks it covered, what fraction have a real time-of-day (a bare date is useless for a
  3:50 PM cutoff), and how fast it ran.

**CLI - query a ticker list, get the filings back** (`scripts/fetch_sec_edgar_news.py`).
This is the "give it tickers, get news" tool. Three output modes:

```bash
# 1) Human table - tickers in, filings out
python scripts/fetch_sec_edgar_news.py --tickers AAPL NVDA JPM \
    --from 2025-09-01 --to 2025-12-31 --user-agent "you you@example.com"
#   AAPL (16 filings)
#     2025-12-05 21:31Z  AAPL files 8-K: 8-K
#     2025-11-14 23:30Z  AAPL files 4: FORM 4  ...
#   Total: 84 filings across 3 tickers.

# 2) JSON (one record per line) - pipe into anything
python scripts/fetch_sec_edgar_news.py --tickers AAPL --from 2025-10-01 --to 2025-12-31 --json
#   {"provider":"sec_edgar","available_at_utc":"2025-12-05T21:31:42+00:00",
#    "headline":"AAPL files 8-K: 8-K","url":"https://www.sec.gov/Archives/.../ef..._8k.htm", ...}

# 3) Save into a NewsStore the evaluator can consume directly
python scripts/fetch_sec_edgar_news.py --tickers AAPL MSFT --from 2025-11-01 --to 2025-12-31 \
    --out data/news/sec_edgar_2025 --user-agent "you you@example.com"
#   Wrote 36 records ...   then:  run_evaluation.py --set news.data_dir=data/news/sec_edgar_2025
```

You can also pass `--data-root data/stock_data_1y` instead of `--tickers` to pull the whole
30-stock evaluation universe, and `--forms 8-K 10-Q` to filter filing types. Verified end to
end: the saved store loads back through the evaluator's own `NewsStore.load_all()`.

**Verify / result.** Running the survey on SEC EDGAR (live):
```
  240 filings - 4/4 stocks covered - 100% with a real, trustworthy timestamp - 0.14s per stock
```
Example: a real Apple 8-K stamped `2025-12-05 21:31:42` with a working link to the filing -
an exact time the government recorded. That is the timestamp integrity the Finnhub feed lacks.

**Left for the organizers (a decision, not code).** Whether they hand agents any news at all
vs. let teams collect their own; GDELT's company-matching accuracy should be measured before
it's trusted; and free-tier history limits (Finnhub only goes back ~1 year) matter for long
backtests - SEC EDGAR avoids that since its history is deep.

---

### #3 - Finalize the participant input/output protocol

**Issue.** The agent is just a Python function the evaluator calls in-process
(`evaluator.py:1024` - `raw_action = agent.decide(observation)`); it returns a bare
`{ticker: weight}` dict. Fine for local tests, but a real competition needs *outside* teams to
connect over a network: an agreed message format, a referee that checks submissions, and a
practice server to integrate against. None of that existed. #3 builds it.

#### The schemas - `schemas/` (JSON Schema draft 2020-12)

The message contract. Four files; required fields listed.

- **`decision_request.schema.json`** - server -> agent:
  `type, protocol_version, run_id, team_id, session_date, deadline_utc, observation`
- **`observation_with_news.schema.json`** - the data inside a request:
  `session_date, event_time_utc, assets[], market_features, fundamental_features,
  portfolio{weights,cash_ratio,nav}, constraints{long_only,max_asset_weight,max_gross_exposure}, news[]`
  (each news item requires `available_at_utc` - the no-leakage boundary)
- **`observation_without_news.schema.json`** - same, but `news` pinned to `maxItems: 0`
- **`decision_response.schema.json`** - agent -> server:
  `type, protocol_version, run_id, team_id, session_date, target_weights` (+ optional `metadata`)

Field names/types match what the evaluator emits (`observation.py:160`), validated against a
real `event_log.json` observation (30 assets / 40 news items).

#### What the validator does - `scripts/validate_protocol.py`

- **Structural check** (`validate_structural`, l.43) - verifies a message matches its schema;
  **rejects** if a required field is missing or a type is wrong (e.g. a weight sent as text).
- **Semantic repair** (`sanitize_target_weights`, l.57 - a port of `risk.py:15`) - takes a
  well-formed but out-of-range portfolio and **fixes it rather than rejecting**, returning the
  list of M9 violation codes. Never raises.
- **Combined check** (`validate_response`, l.103) - runs both layers and returns the repaired
  weights + violations, exactly as the live server should.
- **Self-test** (`--selftest`, l.126) - confirms all four schemas parse and the `1e-9`
  gross-exposure guard works.
- Degrades to a required-key check if `jsonschema` isn't installed (no hard dependency).

The five M9 repair codes it emits (all in `risk.py`): `unknown_asset` -> drop (l.27),
`invalid_number` -> 0 (l.32/35), `short_position` -> 0 (l.38), `asset_cap` -> clip to cap (l.41),
`gross_exposure` -> scale down, with the `+1e-9` fix (l.46-47).

#### One rule for the organizers to ratify
If no response arrives before `deadline_utc`: **carry previous weights** (recommended) rather
than force-to-cash, so a dropped packet doesn't inject a full liquidation. State-machine table
in `SUMMARY_NEXTSTEPS_2_3_6.md`; the mock server's fallback is at `mock_server.py:48`.

#### Verify / result
- `pytest tests -q` -> schema round-trip, malformed-response rejection, no-leakage, and the M9
  cases all pass.
- `python scripts/mock_server.py --event-log outputs/exp2_with_news_10d/llm/event_log.json`
  -> replays 10 sessions through the envelopes end-to-end (`M9 violation rate: 0.0`).

#### Still open (a genuine build)
The production live server - team auth, live scheduling, persistence - is real engineering.
`mock_server.py` is its executable reference (shows the exact loop + validation), not a
substitute for the hardened service.

---

### #6 - Audit unrealistic / fragile parts

**Issues found, and their resolution:**

| # | Issue | Resolution | Verify |
|---|---|---|---|
| 1 | **M9 false positive** - weights summing to `1.0000000000000002` in float64 tripped a spurious `gross_exposure` violation (`risk.py`). Actually fired in a real run. | Added `GROSS_EPSILON = 1e-9` to the gross check. | `pytest tests/test_risk_sanitization.py -q` (`test_float_one_not_flagged`) |
| 2 | **Calendar ignored holidays / early closes** - hard-coded 09:30-16:00, so half-days shifted the decision cutoff wrongly and holidays were miscounted. | New `src/portfolio_agent/nyse_calendar.py` (full holidays incl. Juneteenth, 1 pm early closes, weekend-observation rule), wired into `session_clock` so early closes move the cutoff. | `pytest tests/test_nyse_calendar.py -q` (5 tests) |
| 6 | **Finnhub client fragile** - single `urlopen`, no retry, and the ~250-item response cap silently truncated wide ranges. | 7-day date-chunking + id-dedup + exponential backoff on 429/5xx. | code: `providers/finnhub.py` `_chunk_ranges`, `_get_json(max_retries=...)` |
| 7 | **Provider stubs inert** (`return []`). | Implemented - see #2. | survey command |
| 11 | **No `tests/`** - the README handoff step `pytest tests -q` failed on a fresh clone. | Added `tests/` (19 tests, incl. dedicated lookahead-bias tests). | `pytest tests -q` |

**Confirmed NOT bugs** (checked so no one re-chases them): `adj_open/high/low` are correctly
derived in `data_loader.py`; the point-in-time news filter is correct (its weakness is the
*data*, not the code); train/eval universes are disjoint (good leakage hygiene).

**Deliberately left (with reason):** MOC same-day-close execution (acceptable for feasibility);
the 40-item news cap and horizon length (policy calls); news-trained PPO (data-blocked - no
historical news for the RL universe); the production live server (#9, a real build).

**No regression:** the existing evaluator still runs unchanged with the new calendar wired in
(`scripts/run_evaluation.py ... --set evaluation.horizon_trading_days=3` completes normally).

---

### Files delivered

**New:** `src/portfolio_agent/nyse_calendar.py`; `schemas/` (4 JSON Schemas);
`scripts/validate_protocol.py`, `scripts/survey_news_sources.py`, `scripts/mock_server.py`,
`scripts/starter_kit_agent.py`; `tests/` (4 files, 19 tests); this file and
`SUMMARY_NEXTSTEPS_2_3_6.md`.

**Modified:** `src/portfolio_agent/risk.py` (epsilon), `src/portfolio_agent/market_calendar.py`
(early-close wiring), `src/portfolio_agent/news/providers/{sec_edgar,gdelt,finnhub}.py`.

---

## Part 3 - Deeper analysis: #2, #3, #6 (execution level)


Companion to `RESULTS_SUMMARY.md` (which covers #1, #4, #5). Taken to **execution level**:
the recommendations below are backed by working code that another organizer can run and
replicate, not just prose.

**New/changed files delivered for this document:**

| Area | File | What it is |
|---|---|---|
| #2 | `src/portfolio_agent/news/providers/sec_edgar.py` | Real SEC EDGAR provider (was a `return []` stub). Live-tested. |
| #2 | `src/portfolio_agent/news/providers/gdelt.py` | Real GDELT 2.0 provider (was a stub). Implemented to spec; host unreachable here, so not live-tested. |
| #2 | `scripts/survey_news_sources.py` | Runnable empirical source survey (counts, coverage, timestamp quality, latency). |
| #3 | `schemas/*.json` (4) | Request/observation(+/-news)/response JSON Schemas. |
| #3 | `scripts/validate_protocol.py` | Two-layer reference validator (structural + semantic). |
| #3 | `scripts/mock_server.py` + `scripts/starter_kit_agent.py` | Mock daily server + starter agent; run end-to-end through the envelopes. |
| #3/#6 |  `tests/` (4 files, 19 tests) | Sanitization, calendar, schema round-trip, no-leakage. `pytest tests -q` now passes. |
| #6 | `src/portfolio_agent/nyse_calendar.py` | NYSE holiday + early-close calendar (new). |
| #6 | `risk.py`, `market_calendar.py`, `providers/finnhub.py` | In-place fixes (epsilon, early-close wiring, chunking+retry). |

External-source facts below (rate limits, licensing) are best-known as of this writing and
**must be re-confirmed against each provider's current terms** before any rule is fixed.

---

### #2 - Investigate more public/free news sources

#### Execution status - what now runs

SEC EDGAR and GDELT are **implemented** (no longer stubs), and Finnhub is **hardened**. Live
survey of SEC EDGAR (`python scripts/survey_news_sources.py --source sec_edgar --tickers
AAPL MSFT NVDA JPM --from 2025-06-01 --to 2025-12-31`):

```
  AAPL 23 - MSFT 96 - NVDA 101 - JPM 20   (total 240 filings)
  ticker coverage        : 4/4
  records w/ intraday ts  : 240 (100%)
  authoritative first-seen: 240   (available_at has a real time-of-day, e.g. 8-K @ 21:31:42Z)
  latency                 : 0.14 s / ticker
```

This is the key win: EDGAR's `acceptanceDateTime` gives an **authoritative, non-forgeable**
`available_at_utc` - solving the exact weakness of the Finnhub backfill (finding #4).
GDELT is implemented to the documented DOC-2.0 API but its host was unreachable from the
build environment, so it is **not live-tested** - run the survey from a networked host to
confirm.

#### Where the current code previously stood (the starting point)
- **Finnhub was the only working provider**, with **no pagination / backoff / retry** - now
  fixed with 7-day chunking + exponential backoff (see #6).
- **GDELT and SEC EDGAR were empty `return []` stubs** - now real implementations.
- Evidence from our Exp #4b news inspection (see `RESULTS_SUMMARY.md`): the Finnhub feed is
  ~80% Yahoo aggregator headlines, many market-wide ("Which S&P 500 stocks are moving
  Friday?"), with unreliable ticker mapping (a Northrop headline tagged BA, a SpaceX
  headline tagged T, an Ackman-on-MSFT headline tagged CRM). Per-ticker volume is capped at
  ~250 items (API default, not real coverage), and in the backfill `available_at_utc ==
  published_at_utc` for 100% of items - so it cannot support a no-leakage claim.

#### Candidate-source survey (confirm all figures before committing)

| Source | Cost / license | Public enough? | Intraday latency | Historical depth | Rate limits | Timestamp trust | Ticker mapping | Reproducible for code-check? |
|---|---|---|---|---|---|---|---|---|
| **Finnhub company-news** | Free tier + paid | Yes (free tier) | Minutes | Free ~ 1 yr; paid deeper | ~60/min free | Publish time only (no true first-seen) | Provider-tagged, **noisy** | Only if collection is logged live |
| **SEC EDGAR** | Free, official | Yes | Filing-time (not "news") | Deep (decades) | ~10 req/s, fair-use | **Excellent** (filing timestamp is authoritative) | Via CIK<->ticker map (reliable) | Yes - official + timestamped |
| **GDELT 2.0** | Free | Yes | ~15 min updates | Deep | Generous | Fair, but event vs article time differs | **Weak** - needs entity->ticker validation | Partially; needs pinned snapshots |
| **Alpha Vantage News & Sentiment** | Free tier + paid | Marginal (free tier tiny) | Minutes | Moderate | **~25 req/day free** - too small for 30 tickers/day live | Publish time | Decent, symbol-tagged | Hard on free tier |
| **Alpaca news** | Free w/ account | **Check account terms** | Real-time (websocket) | Moderate | Account-dependent | Good | Symbol-tagged | Only if entitlement is universal |
| **Open RSS / web** | Free, mixed licenses | Varies | Varies | Shallow/none | N/A | **Unreliable** (backdating, re-stamps) | Manual, weak | No |

#### Recommendation for #2
1. **Add SEC EDGAR first.** It is free, official, deeply historical, and its timestamps are
   authoritative - the single best fix for the leakage-timestamp problem (`available_at`).
   Trade-off: it is *filings/regulatory events*, not general market news, so it complements
   rather than replaces a headline feed. Implement CIK<->ticker mapping + filing normalization
   (the stub's TODO).
2. **Treat GDELT as breadth, gated on a ticker-mapping validation pass** - do not turn it on
   until entity->ticker precision is measured on a labelled sample.
3. **Do not rely on Alpha Vantage free tier** for a live 30-ticker day (25 req/day ceiling).
4. **Rule implication:** the competition's "public/free sources only" line conflicts with
   Finnhub's ~1-yr free-history limit if any deep historical training/backtest is required.
   SEC EDGAR sidesteps this; flag the tension explicitly in the rules.

---

### #3 - Finalize the participant input/output protocol

Today the evaluator calls `agent.decide(observation)` with a raw Python dict and gets back a
bare `{ticker: weight}` dict - no envelope, no versioning, no schema. Below is the full wire
protocol to replace that, at implementation level: the two messages, the server loop that
processes them, and a participant skeleton. Artifacts already in the repo: `schemas/` (4 JSON
Schemas) and `scripts/validate_protocol.py` (reference validator), both validated against real
`event_log.json` observations.

#### The daily exchange (one message each way)

```
server  --[1] decision_request  (observation + deadline)-->  agent
agent   --[2] decision_response (target_weights)--------->  server
server: validate -> repair -> execute at close -> account -> log
```

**[1] `decision_request`** - server -> agent (trimmed to 2 of 30 assets for readability):

```json
{
  "type": "decision_request",
  "protocol_version": "0.1",
  "run_id": "official_2026_live",
  "team_id": "team_001",
  "session_date": "2026-06-05",
  "deadline_utc": "2026-06-05T19:50:00+00:00",
  "observation": {
    "session_date": "2026-06-05",
    "event_time_utc": "2026-06-05T19:50:00+00:00",
    "assets": [
      {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "technology", "open_price": 312.86}
    ],
    "market_features": {"AAPL": {"return_1d": 0.004, "momentum_60d": 0.085, "volatility_20d": 0.017}},
    "fundamental_features": {"AAPL": {"net_margin": 0.24, "debt_to_assets": 0.33, "report_age_days": 34}},
    "portfolio": {"weights": {"AAPL": 0.0}, "cash_ratio": 1.0, "nav": 1000000.0},
    "constraints": {"long_only": true, "max_asset_weight": 0.30, "max_gross_exposure": 1.00, "fee_rate": 0.001, "slippage_bps": 0.0},
    "news": [
      {"provider": "finnhub", "provider_news_id": "123456",
       "published_at_utc": "2026-06-05T18:15:00+00:00",
       "available_at_utc": "2026-06-05T18:17:03+00:00",
       "ticker": "AAPL", "tickers": ["AAPL"], "company_names": ["Apple Inc."],
       "headline": "Apple announces a new supplier agreement", "summary": "...",
       "source": "ExampleWire", "url": "https://...", "content_hash": "abc123"}
    ]
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `deadline_utc` | date-time | Hard cutoff. A response received after this is late (see failure table). |
| `observation.event_time_utc` | date-time | The point-in-time boundary. Every `news[i].available_at_utc` <= this. |
| `observation.assets[]` | array | `ticker, company_name, sector, open_price` (open may be `null` if untraded). |
| `market_features` / `fundamental_features` | ticker->{name: number\|null} | Open feature set; nulls allowed. |
| `portfolio` | object | `weights` (current), `cash_ratio`, `nav`. |
| `constraints` | object | `long_only`, `max_asset_weight`, `max_gross_exposure`, `fee_rate`, `slippage_bps`. |
| `news` | array | Empty in the no-news variant (`observation_without_news` pins `maxItems: 0`). |

**[2] `decision_response`** - agent -> server:

```json
{
  "type": "decision_response",
  "protocol_version": "0.1",
  "run_id": "official_2026_live",
  "team_id": "team_001",
  "session_date": "2026-06-05",
  "target_weights": {"AAPL": 0.20, "MSFT": 0.15, "NVDA": 0.10},
  "metadata": {"agent_version": "v1", "used_external_news": true, "external_news_sources": ["sec_edgar"]}
}
```

| Field | Type | Notes |
|---|---|---|
| `target_weights` | ticker->number | Cash is implicit: `cash_weight = 1 - sum weights`. Out-of-range values are repaired, not rejected. |
| `metadata` | object | Optional. `external_news_sources` is a self-declaration hook (unenforced unless audited). |

#### Server-side processing (drop-in, using functions that already exist)

Two layers: **structural** (strict - reject malformed) then **semantic** (lenient - repair,
never reject). Both live in `scripts/validate_protocol.py`; the semantic layer is a port of
`portfolio_agent.risk.sanitize_target_weights`.

```python
from portfolio_agent.execution import ExecutionModel
from scripts.validate_protocol import validate_structural, sanitize_target_weights

def process_response(resp, allowed_tickers, constraints, close_prices,
                     state, prev_weights):
    # 1. STRUCTURAL - reject malformed envelope, fall back to failure policy
    errors = validate_structural(resp, "decision_response.schema.json")
    if errors or resp["session_date"] != today:        # malformed OR wrong day
        target = prev_weights                           # carry-previous (see table)
        violations = ["invalid_response"]
    else:
        # 2. SEMANTIC - repair in-range; record what was repaired for M9
        target, violations = sanitize_target_weights(
            resp["target_weights"], allowed_tickers,
            max_asset_weight=constraints["max_asset_weight"],
            max_gross_exposure=constraints["max_gross_exposure"])  # incl. 1e-9 gross guard

    # 3. EXECUTE at close (market-on-close), 4. ACCOUNT
    trades = ExecutionModel(fee_rate=constraints["fee_rate"]).execute_close(
        state, target, close_prices)
    # 5. LOG raw resp, target, violations -> event_log.json  (M9 = fraction of days repaired)
    return target, trades, violations
```

**M9 violation codes** (emitted by `sanitize_target_weights`):

| Code | Trigger | Repair |
|---|---|---|
| `unknown_asset` | ticker not in universe | dropped |
| `invalid_number` | non-numeric / non-finite | -> 0 |
| `short_position` | weight < 0 | -> 0 |
| `asset_cap` | weight > `max_asset_weight` | -> cap |
| `gross_exposure` | sum > `max_gross_exposure` + 1e-9 | scaled down proportionally |

#### Late / failed response - server default (organizer must ratify)

| Situation | Server action |
|---|---|
| Valid, before `deadline_utc` | sanitize -> execute |
| After `deadline_utc` | reject -> **carry previous weights** (no trade, no cost) |
| No response / disconnect | **carry previous weights** |
| Structurally invalid | reject -> carry previous -> log for team |
| First session (no previous) | carry = **all-cash** |

Recommend **carry-previous over force-to-cash**: a dropped packet shouldn't trigger a full
liquidation that swamps the strategy signal. Fix this before any live run.

#### Participant side (minimal agent)

```python
def decide(request):
    obs = request["observation"]
    # ... your strategy over obs["market_features"], obs["news"], etc. ...
    return {
        "type": "decision_response",
        "protocol_version": request["protocol_version"],
        "run_id": request["run_id"], "team_id": MY_TEAM,
        "session_date": obs["session_date"],
        "target_weights": {"AAPL": 0.2, "MSFT": 0.15},
    }
```

#### Validation status
`python scripts/validate_protocol.py --selftest` passes (4 schemas parse; float-1.0 epsilon
guard confirmed). A real 30-asset/40-news observation validates against `observation_with_news`;
a real no-news observation against `observation_without_news`; a good response is accepted, a
malformed one rejected with field-level errors.

#### Open decisions (organizer policy, not code) & still-to-build
1. Organizers provide news, or BYO-public? (If none, ship the `observation_without_news` contract.)
2. External API calls allowed live, and how is public-vs-private policed?
3. `metadata` required or ignored for scoring?
4. Versioning contract for `protocol_version` (MAJOR.MINOR): minor = additive, major = breaking?

**Delivered (runnable now):** `scripts/mock_server.py` replays a run's `event_log.json`
through the request/response envelopes against `scripts/starter_kit_agent.py`, applying the
exact structural->semantic->execute pipeline; `tests/` (19 tests) covers lookahead/no-leakage, the five
sanitization cases, the epsilon regression, calendar, and schema round-trips - `pytest tests
-q` passes. **Remaining:** the production live server (auth, scheduling, persistence) is a real
build; the mock server is its executable reference.

---

### #6 - Audit unrealistic or fragile parts

Concrete findings with locations, ordered by how much they affect result validity. **Status**
reflects what was fixed in this pass ([x] fixed + covered by a test where applicable).

| # | Finding | Status | Fix delivered / remaining |
|---|---|---|---|
| 1 | **Float M9 false positive** - weights summing to `1.0000000000000002` trip `gross_exposure` (`risk.py:40`) | [x] **fixed** | `GROSS_EPSILON = 1e-9` added; regression test `test_float_one_not_flagged` |
| 2 | **Calendar ignores holidays / early closes** (`market_calendar.py`) | [x] **fixed** | New `nyse_calendar.py` (holidays + 1pm early closes, weekend-observation rule); wired into `session_clock`; 5 tests |
| 6 | **Finnhub client has no pagination / retry** (`providers/finnhub.py`) | [x] **fixed** | 7-day date-chunking + id-dedup + exponential backoff on 429/5xx |
| 7 | **GDELT & SEC EDGAR were inert `return []` stubs** | [x] **fixed** (EDGAR live-tested; GDELT to-spec) | Both implemented; see #2 |
| 11 | **No `tests/`** - `pytest tests -q` failed on fresh clone | [x] **fixed** | `tests/` with 19 passing tests (incl. lookahead-bias) |
| 3 | **Execution is same-day-close MOC** (`execution.py:48`) | [!] by design | Acceptable for feasibility; model impact/auction before the official protocol |
| 4 | **Backfill has no true `available_at`** (available==published) | [!] mitigated | SEC EDGAR now provides authoritative timestamps (#2); Finnhub still needs a live collector |
| 5 | **`max_items_per_decision: 40` binds every day** (`observation.py`) | [ ] open | Make the cap per-ticker, or raise it and log truncation |
| 8 | **PPO policy is news-blind** (`train_ppo.py:41`) | [ ] open (data-blocked) | Needs historical news for the RL universe; unavailable (see `RESULTS_SUMMARY.md`) |
| 9 | **No live SDK / server / auth** | [ ] open (genuine build) | `scripts/mock_server.py` is a runnable reference of the loop; a production server with auth/scheduling is a separate build |
| 10 | **Short windows make risk metrics noisy** (`metrics.py:95`) | [ ] policy | Use >=1-quarter horizons or the multi-horizon average (Exp #1) |

#### Things that are **fine** (checked, not bugs)
- `adj_open`/`adj_high`/`adj_low` are correctly derived from the `adj_close/close` ratio in
  `data_loader.py:42-45` - the CSVs lack an `adj_open` column but the loader computes it.
- The point-in-time news filter (`available_at_utc <= cutoff`) is implemented correctly; its
  weakness is the *data* (finding #4), not the code.
- Leakage hygiene between train and eval is good: the 60-ticker RL universe is disjoint from
  the 30-ticker eval universe (zero symbol overlap).

#### What remains (honest scope)
Findings 1, 2, 6, 7, 11 are **done and tested**. The remaining items are either **by design**
(#3 MOC execution), **policy calls** for the organizers (#5 cap, #10 horizon), **data-blocked**
(#8 news-trained PPO), or a **genuine build project** (#9 the production live server - the mock
server demonstrates the contract but auth, scheduling, and persistence are not something to
stub convincingly). Everything an organizer can replicate today is a runnable script or a
passing test; #9 is the one piece that is a real engineering effort, flagged as such.
