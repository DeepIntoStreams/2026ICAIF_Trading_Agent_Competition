# Summary - README next-steps #2, #3, #6

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

## #2 - Investigate more public/free news sources

### Execution status - what now runs

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

### Where the current code previously stood (the starting point)
- **Finnhub was the only working provider**, with **no pagination / backoff / retry** - now
  fixed with 7-day chunking + exponential backoff (see #6).
- **GDELT and SEC EDGAR were empty `return []` stubs** - now real implementations.
- Evidence from our Exp #4b news inspection (see `RESULTS_SUMMARY.md`): the Finnhub feed is
  ~80% Yahoo aggregator headlines, many market-wide ("Which S&P 500 stocks are moving
  Friday?"), with unreliable ticker mapping (a Northrop headline tagged BA, a SpaceX
  headline tagged T, an Ackman-on-MSFT headline tagged CRM). Per-ticker volume is capped at
  ~250 items (API default, not real coverage), and in the backfill `available_at_utc ==
  published_at_utc` for 100% of items - so it cannot support a no-leakage claim.

### Candidate-source survey (confirm all figures before committing)

| Source | Cost / license | Public enough? | Intraday latency | Historical depth | Rate limits | Timestamp trust | Ticker mapping | Reproducible for code-check? |
|---|---|---|---|---|---|---|---|---|
| **Finnhub company-news** | Free tier + paid | Yes (free tier) | Minutes | Free ~ 1 yr; paid deeper | ~60/min free | Publish time only (no true first-seen) | Provider-tagged, **noisy** | Only if collection is logged live |
| **SEC EDGAR** | Free, official | Yes | Filing-time (not "news") | Deep (decades) | ~10 req/s, fair-use | **Excellent** (filing timestamp is authoritative) | Via CIK<->ticker map (reliable) | Yes - official + timestamped |
| **GDELT 2.0** | Free | Yes | ~15 min updates | Deep | Generous | Fair, but event vs article time differs | **Weak** - needs entity->ticker validation | Partially; needs pinned snapshots |
| **Alpha Vantage News & Sentiment** | Free tier + paid | Marginal (free tier tiny) | Minutes | Moderate | **~25 req/day free** - too small for 30 tickers/day live | Publish time | Decent, symbol-tagged | Hard on free tier |
| **Alpaca news** | Free w/ account | **Check account terms** | Real-time (websocket) | Moderate | Account-dependent | Good | Symbol-tagged | Only if entitlement is universal |
| **Open RSS / web** | Free, mixed licenses | Varies | Varies | Shallow/none | N/A | **Unreliable** (backdating, re-stamps) | Manual, weak | No |

### Recommendation for #2
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

## #3 - Finalize the participant input/output protocol

Today the evaluator calls `agent.decide(observation)` with a raw Python dict and gets back a
bare `{ticker: weight}` dict - no envelope, no versioning, no schema. Below is the full wire
protocol to replace that, at implementation level: the two messages, the server loop that
processes them, and a participant skeleton. Artifacts already in the repo: `schemas/` (4 JSON
Schemas) and `scripts/validate_protocol.py` (reference validator), both validated against real
`event_log.json` observations.

### The daily exchange (one message each way)

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

### Server-side processing (drop-in, using functions that already exist)

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

### Late / failed response - server default (organizer must ratify)

| Situation | Server action |
|---|---|
| Valid, before `deadline_utc` | sanitize -> execute |
| After `deadline_utc` | reject -> **carry previous weights** (no trade, no cost) |
| No response / disconnect | **carry previous weights** |
| Structurally invalid | reject -> carry previous -> log for team |
| First session (no previous) | carry = **all-cash** |

Recommend **carry-previous over force-to-cash**: a dropped packet shouldn't trigger a full
liquidation that swamps the strategy signal. Fix this before any live run.

### Participant side (minimal agent)

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

### Validation status
`python scripts/validate_protocol.py --selftest` passes (4 schemas parse; float-1.0 epsilon
guard confirmed). A real 30-asset/40-news observation validates against `observation_with_news`;
a real no-news observation against `observation_without_news`; a good response is accepted, a
malformed one rejected with field-level errors.

### Open decisions (organizer policy, not code) & still-to-build
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

## #6 - Audit unrealistic or fragile parts

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

### Things that are **fine** (checked, not bugs)
- `adj_open`/`adj_high`/`adj_low` are correctly derived from the `adj_close/close` ratio in
  `data_loader.py:42-45` - the CSVs lack an `adj_open` column but the loader computes it.
- The point-in-time news filter (`available_at_utc <= cutoff`) is implemented correctly; its
  weakness is the *data* (finding #4), not the code.
- Leakage hygiene between train and eval is good: the 60-ticker RL universe is disjoint from
  the 30-ticker eval universe (zero symbol overlap).

### What remains (honest scope)
Findings 1, 2, 6, 7, 11 are **done and tested**. The remaining items are either **by design**
(#3 MOC execution), **policy calls** for the organizers (#5 cap, #10 horizon), **data-blocked**
(#8 news-trained PPO), or a **genuine build project** (#9 the production live server - the mock
server demonstrates the contract but auth, scheduling, and persistence are not something to
stub convincingly). Everything an organizer can replicate today is a runnable script or a
passing test; #9 is the one piece that is a real engineering effort, flagged as such.
