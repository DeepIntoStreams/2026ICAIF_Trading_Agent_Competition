# ICAIF 2026 unified competition deployment

This directory provides the organizer-operated competition server used by both Validation and
the Official Competition. Participants run agents locally and interact with the same authenticated
observation/decision protocol in both phases. Codabench is outside the runtime path: it may be
used for final code-archive intake and, later, as a mirror of organizer-computed leaderboard data.

The current interface and hosting design is recorded in `ARCHITECTURE.md`. The older sections
below describe the previous Codabench-centric prototype and are retained temporarily as migration
context; they are not the current product boundary.

> **Current direction (2026-09-01):** one organizer-controlled competition server, one protocol,
> two clock policies. Validation rapidly replays a historical episode; Official Competition
> advances on the real U.S. trading calendar. Codabench never runs participant agents.

## 1. Two different evaluation paths

The validation and live phases have different execution models and must not be treated as one
stateless Docker job.

### Path A: development and validation

Participants upload code. Codabench starts the evaluation image, runs the code against hidden
2025 data, and reports M1-M9.

```text
participant alpha.zip
        |
        v
Codabench code submission
        |
        v
ingestion_program/run.py
        |
        +-- public observations/features
        +-- hidden prices/configuration
        +-- submitted alpha/agent.py
        |
        v
competition/code evaluation engine
        |
        v
evaluation.json
        |
        v
scoring_program/score.py
        |
        +-- scores.txt / scores.json
        +-- scores.html
        |
        v
Codabench validation leaderboard
```

This path is self-contained and stateless between submissions. Codabench directly starts its
evaluation after a participant submission.

### Path B: official live competition

Participants run agents locally and upload only target weights to Codabench. Codabench is the
participant-facing submission system and authoritative source for team identity and receipt
time. The live server owns cross-day portfolio state and next-open settlement.

```text
Live Server publishes official daily observation
        |
        v
participant downloads observation and runs agent locally
        |
        v
participant uploads submission.json to Codabench
        |
        v
Codabench records team + submitted_at + submission ID
        |
        v
Codabench Bridge imports the accepted submission
        |
        v
Live Server validates and queues target weights
        |
        v
next trading-day open settlement
        |
        +-- cash / shares / transaction fees
        +-- NAV and M1-M9
        +-- complete audit record
        |
        v
official live leaderboard and Codabench synchronization
```

Daily submissions are related: today's decision affects tomorrow's holdings and subsequent
observations. Therefore independent daily Codabench scoring runs cannot be the state owner.

## 2. Repository paths

```text
deployment/
  README.md                         architecture and handoff (this file)
  WORK_PLAN.md                      estimates, remaining work, policy blockers
  docker-compose.yml                local/staging live-server composition

  codabench/
    README.md                       Codabench packaging notes
    competition.yaml                version-2 bundle template
    ingestion_program/
      metadata.yaml                 Codabench command declaration
      run.py                        validation code-submission adapter
    scoring_program/
      metadata.yaml
      score.py                      validation M1-M9 adapter
    live_receipt_scorer/
      metadata.yaml
      score.py                      immediate live JSON receipt check only
    docker/
      Dockerfile                    validation evaluation image
      .dockerignore

  live_server/
    README.md                       operator commands and production gates
    app.py                          authenticated HTTP API
    store.py                        SQLite state and next-open settlement
    manage.py                       organizer administration CLI
    codabench_bridge.py             provider-neutral export importer
    Dockerfile                      live service image

  tests/
    test_live_store.py              state and settlement regressions
```

Existing code reused by deployment:

```text
competition/code/engine.py          official next-open simulation and M1-M9 path
competition/code/runner.py          isolated participant-agent runner
competition/code/panel.py           point-in-time daily observation builder
competition/schemas/                request/response contracts
competition/website/                content for Codabench pages
src/portfolio_agent/metrics.py      M1-M9 calculations
src/portfolio_agent/risk.py         weight validation and repair
src/portfolio_agent/nyse_calendar.py exchange calendar
```

The research package remains the source of reusable financial logic. Deployment-specific APIs,
credentials, state, and platform configuration belong under `deployment/`.

## 3. Responsibility boundary

### Codabench owns

- registration and authenticated team identity;
- public pages, terms, phases, and starter-kit downloads;
- validation code submissions and evaluation jobs;
- live `submission.json` uploads;
- authoritative submission ID and receipt timestamp;
- validation scores and, if the integration supports it, display of live scores.

The live receipt scorer only acknowledges JSON format. Its `receipt_accepted` value is not a
trading score and must not determine final rank.

### Live Server owns

- immutable official daily observations and price snapshots;
- authenticated team-private portfolio observations;
- import using the authenticated Codabench team identity;
- deadline, identity, session, and weight validation;
- idempotency keyed by Codabench submission ID;
- deterministic 10% per-asset and 100% gross-exposure repair;
- latest-valid-submission selection before cutoff;
- retain-previous-target behavior on missing submissions;
- next-trading-day open execution;
- cash, shares, costs, turnover, NAV, violations, and M1-M9;
- settlement audit records and official live leaderboard data.

The uploaded `team_id` is never sufficient authentication. The authenticated Codabench identity
must be supplied by the bridge and match the submitted document.

## 4. Data paths

There are three data classes.

### Shared official observation

Market features, point-in-time fundamentals, official news, universe, constraints, session date,
and cutoff are common to every team. They can be distributed as immutable JSON snapshots or
through the live API.

### Team-private portfolio state

Weights, cash ratio, and NAV differ by team. The live server adds them to the shared observation
after authentication; they must not be exposed to another team.

### Hidden settlement data

Validation execution prices are reference data visible only to the evaluator. Live execution
prices are stored by the server and used at the permitted next-open settlement time. Every
published or settlement artifact should eventually include a content hash and upstream source
version for dispute reproduction.

## 5. Submission paths

### Validation code submission

```text
alpha.zip
  agent.py
  requirements.txt
  MANIFEST.json
  model_weights.*          optional
  proprietary_news/       optional and subject to final policy
```

The adapter accepts `agent.py` at the archive root or under `alpha/`, creates an isolated work
directory, and invokes the official engine. Hidden reference data must never be copied to a path
visible to participant code.

### Live weight submission

```json
{
  "type": "decision_response",
  "protocol_version": "0.1",
  "run_id": "official_2026_live",
  "team_id": "team_001",
  "session_date": "2026-10-27",
  "target_weights": {"AAPL": 0.08, "MSFT": 0.07}
}
```

The current bridge consumes a provider-neutral JSONL export with:

- `codabench_submission_id`;
- `codabench_team_id`;
- `submitted_at`;
- original `submission` JSON.

Only the future provider client/exporter should depend on the actual Codabench API version. The
live state and settlement implementation should remain platform-independent.

## 6. Docker paths and security

Two images are used because their security and dependency requirements differ.

### Evaluation image

Runs untrusted participant code during validation/audit. Production still needs sandbox
confirmation: hidden-path isolation, controlled network, CPU/memory/time limits, child-process
cleanup, dependency allow-listing, and deterministic runtime versions.

### Live-server image

Runs organizer code only. It uses a non-root user and persistent `/data` volume. In production it
must sit behind TLS and an authenticated reverse proxy; administrator endpoints must not be
publicly reachable.

The repository-root `.dockerignore` prevents Git history, local data, models, outputs, web state,
and SQLite files from being sent to the Docker daemon.

## 7. Current status

Implemented:

- Codabench bundle template with validation and live phases;
- validation ingestion and M1-M9 scoring adapters;
- live receipt validation;
- evaluation and live-server Dockerfiles;
- SQLite state, team tokens, daily publication, bridge import, deadlines, and idempotency;
- next-open settlement and leaderboard metrics;
- identity, lateness, repair, duplicate, and next-open tests;
- liquidation of old assets omitted from a new target portfolio.

Verified locally:

- live-store tests pass;
- protocol self-test passes;
- Codabench YAML parses;
- scoring adapter smoke test passes;
- all new Python files parse;
- `git diff --check` passes.

Not verified here:

- Docker build: current user cannot access `/var/run/docker.sock`;
- actual Codabench API/upload behavior: instance details and credentials are unavailable;
- real live publishing: production market/news source is not selected.

## 8. Unknowns and blockers

These are intentionally recorded rather than guessed.

### Codabench integration unknowns

- Target Codabench URL, version, and organizer API capabilities.
- Whether the API exposes submission file, authenticated team, and exact receipt timestamp.
- Whether the bridge polls an API or consumes an organizer-controlled export/event.
- How official live M1-M9 values are written back to the Codabench leaderboard.
- Dataset/program keys and registry image names replacing every `REPLACE_ME`.
- Multiple live submissions per day: allowed count and effective-submission rule.

### Competition-policy unknowns

- M1-M9 equal-rank versus weighted M10 reproducibility score.
- Final live dates; current documents contain conflicting dates.
- Exact daily cutoff and authoritative timezone.
- Allowed LLM names and pinned versions.
- Public/external news rules and audit-upload requirements.
- Slippage/impact and official price-correction policy.
- Disqualification, Top-10 audit, and replacement process.

### Data and infrastructure unknowns

- Authoritative live OHLCV provider and availability SLA.
- Official news provider and collection schedule.
- Hosting, domain, TLS, firewall, and secret store.
- SQLite MVP versus PostgreSQL production database.
- Backups, monitoring, alerting, disaster recovery, and their owners.
- Codabench-team reconciliation and organizer administration workflow.

## 9. Recommended continuation order

1. Freeze policy with the organizers.
2. Obtain access to the target Codabench instance.
3. Create a private test competition and upload one live JSON submission.
4. Confirm retrieval of submission ID, authenticated team, file, and receipt time.
5. Implement the small Codabench-specific client around the stable `LiveStore` interface.
6. Build both images in CI and run tests inside them.
7. Package real validation input/reference datasets and replace all `REPLACE_ME` values.
8. Connect the approved market/news publisher.
9. Deploy staging with TLS, database, backups, secrets, logs, and monitoring.
10. Run a multi-team, multi-day rehearsal covering late, missing, duplicate, malformed, crash,
    restart, holiday, and data-correction cases.
11. Reconcile every Codabench submission against the live database before launch approval.

## 10. Branch and merge relationship

Current work branch:

```text
feat/codabench-live-integration
```

Base branch and commit:

```text
feasibility/protocol-news-audit @ 5dc3773
```

The audit/platform base should enter `main` before or together with this dependent deployment
branch. Do not merge this branch directly into a stale `main` without deciding how the base
history will be integrated, or the deployment PR will also contain the entire platform change.

See `WORK_PLAN.md` for effort estimates and remaining production gates.
