# Codabench bundle

> **Status: previous prototype, not the current runtime architecture.** Codabench no longer
> handles Validation or Official Competition observations, decisions, evaluation, or settlement.
> This directory will be reduced to final code-archive intake and an optional leaderboard mirror
> after that workflow is tested. Do not publish the current `competition.yaml`.

`competition.yaml` is a version-2 bundle template. Build/upload the programs and datasets named
there, then replace every `REPLACE_ME` key with the resulting Codabench dataset key.

Validation is a code-submission task. The ingestion adapter copies the read-only task input to an
isolated work directory, runs the submitted `alpha/agent.py` through the official engine, and
writes `evaluation.json`. The scoring adapter converts that result to Codabench `scores.json`,
`scores.txt`, and detailed `scores.html`.

Live submissions are result submissions containing one `submission.json`. Codabench is the
system of record for identity and receipt time; a provider-specific bridge imports accepted
submissions into `live_server`. Do not expose the live-server organizer token to participants or
inside a public scoring program.

`receipt_accepted` is only an immediate format acknowledgement. The authoritative M1-M9 values
are produced by the stateful live server after next-open settlement and must be synchronized by
the organizer integration; the receipt score must not be used to determine final rank.
