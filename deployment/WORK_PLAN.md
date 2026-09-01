# Deployment work plan and estimate

> Superseded direction as of 2026-09-01: the organizer-operated unified competition server now
> owns both phases, while Codabench is limited to final code archives and a possible leaderboard
> mirror. See `ARCHITECTURE.md`. The older estimates below remain as migration history.

Owner scope: Codabench integration, evaluation images/adapters, and the stateful live server.
Strategy research and competition policy decisions remain outside this deployment branch.

## Current deliverable

- Codabench v2 bundle template with validation and live phases.
- Code-submission ingestion adapter and M1-M9 scoring adapter.
- Live result-submission receipt scorer.
- Evaluation and live-server Dockerfiles.
- SQLite live state, authenticated observation/state API, Codabench JSONL bridge,
  deadline enforcement, idempotency, next-open settlement, and metrics.
- Regression coverage for identity, lateness, cap repair, idempotency, and next-open behavior.

## Remaining integration work

| Work item | Estimate | External dependency |
| --- | ---: | --- |
| Verify target Codabench API/export and implement concrete client | 2-4 days | Instance URL/version and organizer credentials |
| Package/upload bundle datasets and replace dataset keys | 1-2 days | Final 2025 validation dataset |
| Reconcile Codabench user/team identifiers | 1-2 days | Registration/team policy |
| Production market-data publisher | 3-5 days | Approved vendor/source and credentials |
| Codabench live-score/leaderboard synchronization | 2-4 days | Final scoring policy/API capability |
| PostgreSQL migration, TLS, secret management, backup/monitoring | 4-7 days | Hosting environment |
| Multi-team, multi-day dress rehearsal and failure recovery | 4-6 days | Deployed staging Codabench/server |

The code-complete MVP is roughly 3-5 person-weeks. A production launch with staging,
monitoring, recovery testing, and organizer sign-off is roughly 6-9 person-weeks for one
engineer. These are elapsed engineering estimates, not the time needed merely to scaffold files.

## Decisions that block final configuration

1. Whether final scoring is M1-M9 equal-rank or includes weighted M10.
2. Final live dates and the daily deadline in UTC/ET.
3. Which Codabench submission is effective when a team submits multiple times in one day.
4. Allowed models, external data, and news policy.
5. Authoritative market-price/news sources and correction policy.
6. Whether official final ranking is rendered in Codabench or linked from the live service.

Do not replace `REPLACE_ME` values or publish the competition until these are signed off.
