# ICAIF 2026 Trading Agent Competition — Official Starter Kit

This kit supports the complete Registration → Validation → Official → Final workflow for [Competition 99](https://hackathon2.deepintomlf.ai/competitions/99/). It preserves the 30-stock universe, seven ET rounds per trading day, USD 1,000,000 independent Validation and Official accounts, the 0.1% transaction fee, the 0.30 per-stock weight cap, and the official four-metric scoring formula.

The included `profiles/profile99-production.json` points to the live Competition 99 unified phase. Keep credentials and checkpoints private; this repository contains no team token or Codabench account token.

## Manual upload files

The kit root contains the exact three filenames accepted by Competition 99:

- `register.json`: replace the team name, captain email, and member details, then upload it during Registration.
- `decision.json`: after Registration, replace `team_id` and `team_token`, select the current `phase` and `round_id` from the live schedule, update all 30 target weights, then upload it inside that round's window.
- `final_submission.json`: replace the same team credential, complete member list, HTTPS shared-materials link, and exact file inventory before the Final deadline.

Keep these filenames unchanged. The included values are safe placeholders and cannot authenticate until you replace them with your own registration result.

## Quick start

Requires Python 3.10+. Run commands from this extracted directory.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally with your personal `CODABENCH_TOKEN` and the actual `ICAIF_PROFILE` path. Keep `.env`, `.icaif/`, live decisions, and your materials private. Never upload credentials, an entire project folder or a ZIP as a competition submission.

Prepare your own members and contact information in `private/register.json`, using `examples/registration/register.json` as the exact structural template. Replace all demo values, then:

```sh
python tools/auto_submit.py register --file private/register.json
python tools/auto_submit.py schedule
python tools/auto_submit.py portfolio --phase validation
```

Registration uploads one raw UTF-8 JSON object named **register.json**. Its first private receipt supplies `TEAM_ID` and the one-time `TEAM_TOKEN`, saved atomically with mode 0600 in `.icaif/credentials.json`. The CLI prints a redacted receipt. Personal platform token and backend team token have different purposes; `decision.json` and Final still require both original team fields.

Prepare your real 30-symbol weights in the original format and submit inside its exact round window:

```sh
python tools/auto_submit.py decision --file private/decision.json
python tools/auto_submit.py round --round-id validation-2026-10-08-r1
python tools/auto_submit.py decisions --phase validation
python tools/auto_submit.py metrics --phase validation
```

All status, schedule, portfolio, round, decision-history and metric queries use GET. They create no submission. First attributable in-window upload consumes a round even when invalid; a second upload cannot correct that slot. Missing decisions hold the current portfolio; zero weights liquidate to cash under the original execution rules.

For automation, provide your own local strategy callable and legitimate data. The bundled file strategy only reads weights that you prepared explicitly for the current round:

```sh
python tools/auto_submit.py watch --phase validation --strategy examples.automation.file_strategy:strategy --once
python tools/auto_submit.py watch --phase official --strategy examples.automation.file_strategy:strategy --max-wait-seconds 86400
```

Set `ICAIF_WEIGHTS_FILE` as described in `examples/automation/file_strategy.py`. Watch refreshes the organizer schedule, passes the current round and own portfolio to your strategy, validates all 30 weights, and uploads one **decision.json** per available round. Reuse the same checkpoint when restarting. Default watch duration is one hour; use a longer explicit duration as needed.

After the last Official close, supply a genuine, accessible HTTPS materials link, actual filenames and complete team members in **final_submission.json**:

```sh
python tools/auto_submit.py final --file private/final_submission.json
python tools/auto_submit.py fetch --submission-id YOUR_ORIGINAL_FINAL_ID
```

Final is explicit and one accepted material record cannot be overwritten. The client does not download or execute your materials, generate pretend files, or automatically submit Final. The original inclusive deadline is November 3, 2026, 23:59 ET. A pending or frozen scorer receipt is not public score release: final scores wait for organizer review, complete inventory, freeze, same-ID re-score, verification, backend publication and separate platform reveal. Official private metrics remain gated by the server until publication; portfolio/ledger accounting stays available.

## Recovery and reference

After an interruption, rerun the **same command with the same checkpoint and unchanged file**, or use `fetch` for the saved original submission ID. An ambiguous creation is resolved with read-only platform inventory plus owner, phase, filename and raw SHA256 checks; creation POSTs are never automatically retried. Keep the checkpoint if a worker fails; organizer recovery uses the same submission, not a new upload.

- [Automatic commands, recovery and Python API](docs/automatic_submission.md)
- [中文自动提交流程](docs/automatic_submission_zh.md)
- [Original participant guide](docs/guide.md), [rules](docs/rules.md), [evaluation](docs/evaluation.md)
- [Profile configuration](profiles/README.md)
- [Original manual tools](tools/prepare_submission.py), [original validation](tools/validate_submission.py), [local evaluator](tools/evaluate.py)

The bundled `examples/synthetic_prices.json` is synthetic and intended only for offline examples. It is not official market data.
