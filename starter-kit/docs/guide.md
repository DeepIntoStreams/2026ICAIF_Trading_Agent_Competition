# Participant guide

Sign in to the [competition website](https://hackathon2.deepintomlf.ai/competitions/99/) to register and upload submissions. Use the separate participant API address published by the organizer for `TRADING_API_BASE_URL`. Check competition announcements for schedule updates and final-material requirements.

## Set up the kit

Use Python 3.11 or later from the `starter kit` directory. The kit uses only the Python standard library, so no packages need to be installed.

Copy `.env.example` to `.env`. Keep `.env` and generated submissions private. The preparation and API tools read these values without putting credentials in command arguments:

```text
TEAM_ID=
TEAM_TOKEN=
TRADING_API_BASE_URL=
```

Use `--allow-placeholders` only to inspect a bundled template before credentials and participant fields have been filled:

```console
python tools/validate_submission.py examples/registration/register.json --allow-placeholders
```

## 1. Register

Edit `examples/registration/register.json` with the team name, contact email, and every member's name, email, and institution. Validate it without `--allow-placeholders`, then upload the single UTF-8 file named `register.json` to the Registration phase:

```console
python tools/validate_submission.py examples/registration/register.json
```

The private detailed result shows the `team_id` and team token once. Save both in `.env`; do not place the token in source control, screenshots, logs, shared materials, or a public result. A registration rerun does not reveal it again. A lost token requires an organizer reset.

## 2. Run the Validation cycle

Validation and Official use separate portfolios. Validation begins with USD 1,000,000 cash and is the place to rehearse the complete loop:

1. Check the current round and deadline in `schedule.json` and, after the API base URL is published, refresh the live schedule.
2. Obtain permitted data independently and run your strategy. The included rule-based agent is an offline example; see `docs/rule_based_agent.md`.
3. Prepare a canonical `decision.json`, validate it against the schedule, and inspect all 30 target weights.
4. Upload only that JSON file to the correct Codabench phase before the round deadline.
5. Read the receipt, then query the portfolio or round after execution.

Example preparation and validation:

First edit the template's `weights` to the complete 30-symbol target produced by your agent for this round. `prepare_submission.py` inserts credentials and writes the upload file; it does not run a strategy or replace the example weights.

```console
python tools/prepare_submission.py examples/validation/decision.json --output output/validation-2026-10-08-r1/decision.json
python tools/validate_submission.py output/validation-2026-10-08-r1/decision.json --submitted-at 2026-10-08T09:09:00-04:00 --schedule schedule.json
```

The platform upload timestamp decides timeliness. For a round, the earliest uploaded attempt by platform timestamp and sequence consumes the slot even if it is invalid; later attempts are duplicates. Platform processing may be delayed, so a receipt can remain pending before selection or execution. Never rely on a second upload to correct the first.

When the participant API is available, `schedule` and `check` use public configuration; the other commands read private state with `TEAM_TOKEN`. Add `--base-url` to override `TRADING_API_BASE_URL` or `--output FILE` to save JSON.

```console
python tools/api.py check
python tools/api.py schedule --output output/live-schedule.json
python tools/api.py portfolio --phase validation
python tools/api.py decisions --phase validation
python tools/api.py round --round-id validation-2026-10-08-r1
python tools/api.py metrics --phase validation --output output/validation-metrics.json
```

Validation metrics are provisional and include completed decision periods only. A newer portfolio valuation does not close an unfinished period.

## 3. Trade in the Official phase

Official starts again from USD 1,000,000 cash with no Validation positions or returns. Use the Official round ID and phase in every decision:

```console
python tools/prepare_submission.py examples/official/decision.json --output output/official-2026-10-12-r1/decision.json
python tools/validate_submission.py output/official-2026-10-12-r1/decision.json --submitted-at 2026-10-12T09:09:00-04:00 --schedule schedule.json
python tools/api.py portfolio --phase official
python tools/api.py decisions --phase official
```

Official metrics remain unavailable through the private metrics API until all Official trading is complete. Final ranks also require an accepted final submission and organizer approval.

## 4. Submit final materials

Final submission opens after the last Official close. Prepare the shared reproduction folder using `examples/final/README.md` and `examples/final/disclosures.md`. Edit the final template so its member list is complete, its `materials_url` is an HTTPS link accessible to organizers, and its `materials` list exactly describes the shared files.

```console
python tools/prepare_submission.py examples/final/final_submission.json --output output/final/final_submission.json
python tools/validate_submission.py output/final/final_submission.json
```

Upload the single file named `final_submission.json` to the Final Submission phase. The deadline is `2026-11-03T23:59:00-05:00`; unlike decision deadlines, that exact platform timestamp is accepted. The first response is normally `PENDING_REVIEW`. Official scores appear after organizer review and publication; no extra upload is needed while review is pending.

You can reproduce metrics from the synthetic example or from a metrics API response saved as JSON:

```console
python tools/evaluate.py examples/evaluation.json
python tools/evaluate.py output/validation-metrics.json
```

## Short FAQ

**May I upload a ZIP?** No. Each Codabench submission contains exactly one top-level UTF-8 JSON file with the required canonical name.

**Do weights describe trades?** No. They are target portfolio weights. The backend derives trades from the current portfolio and official execution prices.

**What does unused weight mean?** The difference between total stock weight and 1 remains cash. An all-zero valid target liquidates stock positions. A late or duplicate receipt does not replace the selected attempt; when there is no selected valid decision, the existing portfolio is held.

**Why is execution not visible immediately?** Uploads may take time to process, and trades execute at the round's scheduled time. Query the receipt and round rather than repeatedly uploading.

**What do the status fields mean?** `validation_status` records `VALID`, `INVALID`, `LATE`, or `MISSING`; `selection_status` can be `PENDING`, `SELECTED`, `DUPLICATE`, or `NOT_ELIGIBLE`; `execution_status` becomes `EXECUTED`, `HELD`, or `EXECUTION_FAILED`. The top-level receipt `status` presents a later attempt marked as a duplicate as `DUPLICATE` while preserving its original validation result separately.

**Can I use an LLM or external data?** LLM use is optional. Follow `docs/llm_and_external_data.md`, use only permitted sources and models, and disclose actual use in the final materials.

**Are local metrics official?** No. Local evaluation helps you understand and check results. The competition publishes official metrics and ranks after trading and organizer review are complete.
