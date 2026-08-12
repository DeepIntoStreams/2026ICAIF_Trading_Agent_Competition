# Implementation summary - README next-steps #2, #3, #6

What was broken, what was built to fix it, and the exact command to verify each fix. This is
the "what I did" companion to `SUMMARY_NEXTSTEPS_2_3_6.md` (the deeper analysis). Everything
here is runnable from a clean clone.

## 30-second verification

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

## #2 - More public/free news sources

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

## #3 - Finalize the participant input/output protocol

**Issue.** The agent is just a Python function the evaluator calls in-process
(`evaluator.py:1024` - `raw_action = agent.decide(observation)`); it returns a bare
`{ticker: weight}` dict. Fine for local tests, but a real competition needs *outside* teams to
connect over a network: an agreed message format, a referee that checks submissions, and a
practice server to integrate against. None of that existed. #3 builds it.

### The schemas - `schemas/` (JSON Schema draft 2020-12)

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

### What the validator does - `scripts/validate_protocol.py`

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

### One rule for the organizers to ratify
If no response arrives before `deadline_utc`: **carry previous weights** (recommended) rather
than force-to-cash, so a dropped packet doesn't inject a full liquidation. State-machine table
in `SUMMARY_NEXTSTEPS_2_3_6.md`; the mock server's fallback is at `mock_server.py:48`.

### Verify / result
- `pytest tests -q` -> schema round-trip, malformed-response rejection, no-leakage, and the M9
  cases all pass.
- `python scripts/mock_server.py --event-log outputs/exp2_with_news_10d/llm/event_log.json`
  -> replays 10 sessions through the envelopes end-to-end (`M9 violation rate: 0.0`).

### Still open (a genuine build)
The production live server - team auth, live scheduling, persistence - is real engineering.
`mock_server.py` is its executable reference (shows the exact loop + validation), not a
substitute for the hardened service.

---

## #6 - Audit unrealistic / fragile parts

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

## Files delivered

**New:** `src/portfolio_agent/nyse_calendar.py`; `schemas/` (4 JSON Schemas);
`scripts/validate_protocol.py`, `scripts/survey_news_sources.py`, `scripts/mock_server.py`,
`scripts/starter_kit_agent.py`; `tests/` (4 files, 19 tests); this file and
`SUMMARY_NEXTSTEPS_2_3_6.md`.

**Modified:** `src/portfolio_agent/risk.py` (epsilon), `src/portfolio_agent/market_calendar.py`
(early-close wiring), `src/portfolio_agent/news/providers/{sec_edgar,gdelt,finnhub}.py`.
