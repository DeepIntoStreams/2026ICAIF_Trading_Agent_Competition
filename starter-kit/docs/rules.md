# Trading rules and schedule

## Portfolio rules

- Validation and Official each start with USD 1,000,000 cash in independent accounts.
- The portfolio is long-only. Short selling and leverage are not allowed.
- Fractional shares are allowed.
- Every decision must contain all 30 symbols from `../universe.json`, including zero targets and no unknown symbols.
- Each target weight is a finite JSON number from 0 through 0.30. Numeric strings, `NaN`, and infinity are invalid.
- The sum of stock weights must not exceed 1. Unallocated weight remains cash.
- Weights are target portfolio weights. The backend determines buys and sells from the existing holdings.
- Each rebalance charges 0.1% of total buy-plus-sell notional. The initial allocation from cash is included.
- The portfolio carries forward between rounds and overnight. Positions are not forcibly liquidated at the final close.
- A valid all-zero decision targets all cash. A late or duplicate receipt does not replace the earliest selected attempt. If that selected attempt is invalid, or the team has no eligible attempt, the round performs no rebalance, charges no transaction fee, and keeps the existing holdings.

The announced universe has five stocks in each of six groups: Technology, Finance, Healthcare, Consumer, Industrial & Energy, and Communication & Utilities. `../universe.json` is the machine-readable symbol source for templates and validation. Sector labels organize the list; they do not impose allocation quotas.

## Standard daily schedule

All times are U.S. Eastern Time using `America/New_York`.

| Round | Window opens | Submission deadline | Execution |
| --- | --- | --- | --- |
| 1 | Previous trading day's last active execution + 10 min; normally 15:40 | 09:10 | 09:30 market open |
| 2 | 09:40 | 10:25 | 10:30 hourly open |
| 3 | 10:40 | 11:25 | 11:30 hourly open |
| 4 | 11:40 | 12:25 | 12:30 hourly open |
| 5 | 12:40 | 13:25 | 13:30 hourly open |
| 6 | 13:40 | 14:25 | 14:30 hourly open |
| 7 | 14:40 | 15:25 | 15:30 hourly open |

Decision windows include their opening and exclude their deadline. The authoritative platform upload time determines eligibility, regardless of worker delay. A deadline-to-next-opening gap is late for the preceding round. The earliest attributable attempt within a window consumes the round even if invalid; later attempts cannot replace it. The 16:00 official market close is a valuation only, with no submission or rebalance.

Round 7 holdings continue overnight until the next trading day's Round 1 execution boundary. On the last day of a phase, the final period ends at the official close. On an early-close day, rounds whose execution time is at or after the adjusted close are cancelled and the last executed portfolio is valued at that close. Check `../schedule.json` and the live schedule before every submission.

## Competition phases

| Phase | Current configured dates | Purpose |
| --- | --- | --- |
| Registration & Development | September 20, 00:00 ET through October 8, 00:00 ET exclusive | Create the team in time for Validation and Official |
| Live Validation | October 8–9, 2026 | Two-day, 14-round rehearsal with a separate portfolio |
| Official registration only | October 8, 00:00 ET through October 12, 00:00 ET exclusive | Create the team for Official; Validation is no longer available |
| Official Competition | October 12–30, 2026 | Fifteen trading days and normally 105 decision rounds |
| Final Submission | October 30, 16:00 ET through November 3, 2026 at 23:59:00 ET inclusive | Submit reproduction materials once for review |

Cancelled rounds are excluded from evaluation-period counts. The configured Final Submission boundary is inclusive at exactly `2026-11-03T23:59:00-05:00`; this exception does not change the exclusive decision deadlines.

Registration and final uploads outside their windows are ignored with `EARLY` or `LATE` and consume no slot. For final materials, the earliest attributable in-window attempt consumes the only slot even if invalid. There are no replacement finals. Selection waits for the complete platform upload inventory, followed by manual review of valid selected materials, frozen rankings, rerunning the original submission, score verification and organizer release.

Participants obtain their own permitted inputs. This kit contains synthetic prices only and does not fetch or provide a live market feed. The allowed LLM and external-data policy, including disclosure duties, is in `llm_and_external_data.md`.
