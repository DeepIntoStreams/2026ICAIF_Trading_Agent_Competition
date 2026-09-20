# Original protocol automation

The upload contracts are unchanged:

| Command | Raw file | Exact top-level fields |
|---|---|---|
| `register` | `register.json` | `submission_type`, `team_name`, `team_members`, `contact_email` |
| `decision` / `watch` | `decision.json` | `submission_type`, `team_id`, `team_token`, `phase`, `round_id`, `weights` |
| `final` | `final_submission.json` | `submission_type`, `team_id`, `team_token`, `team_members`, `materials_url`, `materials` |

Types are `register`, `decision`, `final_submission`. Each member contains exactly `name`, `email`, `institution`. Weights contain every original stock, including zero targets; values remain exact JSON numbers, including `Decimal` in Python. Raw file uploads preserve the original bytes and filename. Nothing is zipped for upload. The private scorer output is a platform ZIP containing machine-readable `result.json`; the client reads it without extracting files.

## Credentials and deployment

`CODABENCH_TOKEN` is your personal platform API token. Platform upload and native submission reads use `Authorization: Token ...`. Gateway private GET uses that platform token plus `X-ICAIF-Team-Token`. A separately published backend uses only `Authorization: Bearer TEAM_TOKEN`; no personal platform token is sent there or to signed storage URLs. Redirects are refused.

Registration saves `team_id` and one-time `team_token` in `.icaif/credentials.json` before returning a redacted receipt. Existing manual registrations or an audited organizer token reset can be imported from your local environment:

```sh
python tools/auto_submit.py import-credentials
```

Set `TEAM_ID` and `TEAM_TOKEN` in the private environment first. This command does no network call and never prints the token. It refuses to replace a different team's credentials. Preserve the same checkpoint and credentials paths. Changing account token or deployment changes the state scope and is deliberately refused for an existing checkpoint; coordinate account-token rotation and original-ID recovery with the organizer instead of starting new submissions blindly.

## Commands

All global flags precede the command. `--profile` may be omitted if `ICAIF_PROFILE` is set. `--checkpoint` and `--credentials` default to `.icaif/checkpoint.json` and `.icaif/credentials.json`. `--output private/result.json` stores redacted output atomically with mode 0600. Query responses may still contain private portfolio and member information.

| Command | Arguments | Behavior |
|---|---|---|
| `register` | `--file private/register.json` | One team registration; saves the first credential receipt |
| `decision` | `--file private/decision.json` | Checks live round, original fields, weights, own credential and occupied slot |
| `watch` | `--strategy module:callable --phase validation\|official` | Runs the supplied local strategy once per open slot |
| `final` | `--file private/final_submission.json` | Explicit material record within the Final window |
| `fetch` | `--submission-id ID` | Read fresh own receipt for an existing ID |
| `resolve` | `--operation KEY --submission-id ID` | Verify and attach original ID to a pending operation |
| `schedule` | — | GET live original rounds and policy |
| `portfolio` / `ledger` | `--phase validation\|official` | GET own accounting |
| `decisions` | `--phase ... --offset 0 --limit 200` | GET paginated own decisions |
| `round` | `--round-id official-2026-10-12-r1` | GET own attempts, trades and execution snapshot |
| `metrics` | `--phase validation\|official` | GET private metrics subject to server publication gates |

## Calendar and slots

The preserved normal calendar has 14 Validation rounds on October 8–9 and 105 Official rounds on October 12–30. ET deadlines are 09:10, 10:25, 11:25, 12:25, 13:25, 14:25, 15:25. The first round opens at local midnight; later rounds open at the prior deadline. Upload eligibility is `opens_at <= server time < deadline`; Final deadline is inclusive. Execution times and 16:00 valuation are separate from upload deadlines.

Watch reads the live schedule and server clock, including organizer cancellations. It uses portfolio and the current round's private state as strategy inputs; it supplies no invented market data. You must obtain legitimate data in your strategy. Always follow the dates returned by the live Competition 99 schedule.

A first attributable in-window invalid upload occupies its slot. An old round file stamped at its exact deadline is LATE and cannot consume the next round; the client rejects that stale file before upload. Backend receipts explicitly marked NOT_ELIGIBLE do not occupy an available slot. Existing own attempts, including INVALID, prevent a second strategy upload. Missing decisions hold the portfolio. Resuming watch first recovers pending older decision IDs, then proceeds to an available current round. It does not replace invalid attempts or upload a status probe. Use `--once` for one pass, `--max-wait-seconds` to bound a run, and `--poll-seconds` to control read-only polling. Ctrl-C preserves recovery state.

## Interruptions and private files

Before every non-idempotent creation, the client synchronizes a private checkpoint. It includes profile/account hashes, phase, exact filename, exact input SHA256, pending original bytes, dataset key, and original submission ID when known. Personal platform tokens are not persisted. Pending bytes contain your team token, so protect the whole `.icaif/` directory. Atomic writes are mode 0600; one process holds a checkpoint lock.

Safe GETs and replay of identical bytes to the same newly allocated storage object retry at most three times. Dataset-completion PUT confirms that immutable object. Dataset creation and submission creation POSTs never auto-retry.

- If submission creation timed out, rerun the same command with the same file. Read-only inventory inspects your configured phase and matches trusted receipt owner/filename/SHA256. One unique match restores the original ID.
- If no unique match is available, the operation remains blocked. Inspect the platform and consult the organizer; do not delete the checkpoint to force a second upload.
- To attach a verified ID explicitly: `python tools/auto_submit.py resolve --operation registration --submission-id ID`. Decision keys are `decision:<phase>:<round_id>`; Final uses `final_submission`.
- Dataset-allocation ambiguity is stopped before any automatic submission retry. Ask the organizer to resolve the allocated object/checkpoint. An orphan dataset does not itself consume a round.
- Failed or pending worker execution uses the same original ID. `fetch` is read-only. Organizers can recover workers without asking for a new submission.
- Registration repeated with another upload cannot retrieve a new team token. Keep the first private result/credential file; if lost, request the audited organizer reset.

The trusted gateway receipt contract is `GET /extensions/icaif2026/<competition_id>/submissions/<id>/receipt`, with `submission_id`, `owner_username`, `phase_id`, `file_name`, raw `sha256`, `status`, and optional private `result`. Native `/api/my_profile/` supplies the authenticated username. Both native phase/owner and gateway immutable metadata are checked before downloading private output or attaching a pending ID.

## Python API

```python
import os
from kit.original_client import OriginalSession, load_profile

with OriginalSession(
    profile=load_profile(os.environ['ICAIF_PROFILE']),
    token=os.environ['CODABENCH_TOKEN'],
    checkpoint='.icaif/checkpoint.json',
    credentials='.icaif/credentials.json',
) as client:
    schedule = client.schedule()
    portfolio = client.portfolio('validation')
    # Explicit original files, prepared by you:
    # registration = client.register('private/register.json')
    # decision = client.decision('private/decision.json')
    # final = client.final('private/final_submission.json')
    # result = client.fetch(original_submission_id)
```

The same object exposes `decisions`, `round`, `metrics`, `ledger`, `resolve` and `watch`. A strategy function takes `{phase, round, symbols, as_of, portfolio, round_state}` and returns all 30 numeric weights; Python `Decimal` is supported without float conversion. Strategy exceptions are redacted and create no upload. `examples/automation/file_strategy.py` reads your own per-round weight file, and `examples/automation/python_api.py` demonstrates read-only queries and explicit submission.

## Scores and original local tools

Registration, decisions and pending Final receipts carry no competition scores. `PUBLISHED_READY` can describe a frozen scorer receipt ready for controlled re-score; it alone does not prove public release. Official metrics remain server-gated until organizer publication, while visible cash/NAV/P&L may allow independent return calculations. Use `fetch` to refresh a Final receipt; reissuing `final` with the same file returns its saved submission receipt without uploading another record.

The original `tools/evaluate.py`, `kit/evaluation.py`, `tools/validate_submission.py`, `tools/prepare_submission.py`, rule-based agent, schemas, universe, calendar and examples remain intact. Local example metrics and Validation provisional metrics are not the public Final leaderboard. The original guide's attachment URLs are historical context; use the selected deployment profile for automation.
