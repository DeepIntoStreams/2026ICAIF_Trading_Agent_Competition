# Rule-based example agent

This example turns participant-prepared observations into a local competition decision JSON. It uses a small, deterministic momentum rule and does not use an LLM. Its purpose is to demonstrate an agent's input, decision rule and output; it makes no performance claim.

Participants obtain their own permitted data and are responsible for the [LLM and external data policy](llm_and_external_data.md). This guide describes the local input format only. The included observations are synthetic, not historical market prices or an organizer-provided live feed.

## Decision rule

For each symbol in `universe.json`:

1. Keep observations whose market timestamp and availability timestamp are no later than the requested `--as-of` time.
2. Sort the eligible observations by market timestamp and take the most recent six.
3. If fewer than six remain, give that stock a zero target.
4. Otherwise calculate five-interval momentum as `latest_close / oldest_close - 1`.
5. Rank strictly positive momentum values from largest to smallest; break equal scores alphabetically by symbol.
6. Select at most five stocks and give each a target weight of `0.20`. All other targets are zero.

For example, six closes of `100, 101, 102, 103, 104, 110` produce momentum `110 / 100 - 1 = 0.10`. If only three stocks qualify, they receive 20% each and the remaining 40% stays in cash. If no stock qualifies, all 30 targets are zero.

The output always includes all 30 announced symbols. Every target is within the 30% single-stock cap, and total stock weight is at most 1. These are target portfolio weights, not buy/sell order sizes; the competition backend determines trades and transaction fees from the actual portfolio.

An all-zero target requests liquidation to cash if positions already exist. It is different from preserving the existing shares. Missing or insufficient observations can therefore cause existing positions to be sold under this example rule. Review the generated weights before a live submission.

## Setup

Use Python 3.11 or later and open a terminal in the `starter kit` directory. No extra packages are required. The bundled calendar stores explicit UTC offsets for the published Eastern-time schedule.

## Run the offline demonstration

```console
python agents/rule_based_agent.py --data examples/synthetic_prices.json --as-of 2026-10-08T09:09:00-04:00 --phase validation --round-id validation-2026-10-08-r1 --output output/demo/decision.json --demo
```

This historical demonstration uses the fictional observations in the kit. `--demo` writes explicit placeholder credentials. It does not upload a submission, contact a model or execute trades. The resulting JSON is for inspection and cannot authenticate as a team.

Open `output/demo/decision.json` to inspect its `submission_type`, `team_id`, `team_token`, `phase`, `round_id` and complete `weights` object. The agent keeps diagnostic messages separate from the decision file. The included sample selects **AAPL, BAC, CAT, CRM and GE**, each at **0.20**, with zero targets for the remaining 25 symbols.

The agent never overwrites an existing output. To rerun the demonstration, choose a new folder such as `output/demo_second/decision.json`.

## Prepare your own local input

The input JSON has an `observations` object. Its keys are symbols from `universe.json`; a subset is permitted. Each symbol maps to a list of records with these fields:

| Field | Meaning |
| --- | --- |
| `timestamp` | The observation's market time, including an explicit UTC offset. For a completed bar close, use its closing time, not its opening time. |
| `available_at` | The time that this observation or data revision became available to the agent, including an explicit offset. It must be at or after `timestamp`. |
| `close` | A positive, finite JSON number. Numeric strings are not accepted. |

An individual record looks like this:

```json
{
  "timestamp": "2026-10-07T16:00:00-04:00",
  "available_at": "2026-10-07T16:01:00-04:00",
  "close": 110.0
}
```

Use a consistent interval and price convention for comparable signals. The algorithm measures five observation intervals; it does not assume that an interval is one hour or one day. The bundled file is a complete format example.

The agent validates supplied records, including records that are too late to influence the decision. Malformed prices or timestamps, unknown symbols, duplicate JSON keys and duplicate market timestamps for a symbol are input errors. Valid future observations are excluded from the signal. To represent corrected data in this simple format, provide one selected version per market timestamp with its actual availability time.

The timestamps in a local file are declarations, not independent proof of source availability or licensing. Preserve source and access-method records for the final materials as required by the competition policy.

## Generate a decision with team credentials

Set `TEAM_ID` and `TEAM_TOKEN` in your private `.env` using the credentials returned after registration. Existing process environment values take precedence. Do not put the token into command-line arguments or commit it to the kit.

Run the same command with your input file, correct phase/round, actual information cutoff and a new output path, omitting `--demo`. Use the required filename **`decision.json`**, for example `output/validation-2026-10-08-r1/decision.json`. The agent reads the two credential variables and writes a local decision file. That file contains the team token and must be kept private. The kit's `.gitignore` excludes `output/`.

For each round:

1. Consult the published competition schedule for the correct phase, round and any exceptional session.
2. Prepare your local observations and choose an `--as-of` timestamp representing the information available when the decision is made.
3. Run the agent and inspect the complete target weights.
4. Upload only `decision.json` to the correct Codabench phase before the deadline. Keep input observations, scripts and provenance records separate from this single-file decision upload.
5. Inspect the receipt and then the executed portfolio when processing completes.

The script checks `--as-of` against `schedule.json`, including the 09:10 Round 1 deadline. After downloading an updated calendar, pass `--schedule output/live-schedule.json`; cancelled rounds are rejected. The platform upload timestamp remains authoritative: generating a file before the deadline does not make a later upload timely.

## Modify the rule

Start with the scoring and selection logic in `agents/rule_based_agent.py`. Replace the momentum rule with your own decision logic while preserving the announced symbol set, valid target weights and decision JSON fields. Keep the observation-availability handling explicit so later information cannot influence an earlier decision.

LLM use is optional. If you add an LLM, use a model permitted by the policy and include the required model, prompt, inference and API disclosures in the final materials. For this unmodified example, identify the agent as rule-based with no LLM and document any external sources you actually use.
