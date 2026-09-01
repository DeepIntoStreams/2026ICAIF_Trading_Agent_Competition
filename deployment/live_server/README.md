# Unified competition server

This organizer-controlled service supports both Validation and the Official Competition.
Participants run agents locally, authenticate directly with a team API key, receive observations,
and return target weights. Codabench is not in this runtime path.

Both phases share the same protocol and execution engine. Validation will advance through a
configured historical episode immediately; Official Competition advances one real trading day at
a time. See `deployment/ARCHITECTURE.md` and `deployment/API.md`.

## Local operator flow

```bash
python -m deployment.live_server.manage --db /tmp/icaif.sqlite register-team team_001
python -m deployment.live_server.manage --db /tmp/icaif.sqlite publish observation.json market.json
python -m deployment.live_server.manage --db /tmp/icaif.sqlite settle 2026-10-27
python -m deployment.live_server.manage --db /tmp/icaif.sqlite leaderboard
```

Run the API with a secret organizer token:

```bash
LIVE_ADMIN_TOKEN='replace-with-secret' python -m deployment.live_server.app \
  --db /tmp/icaif.sqlite --host 127.0.0.1 --port 8080
```

`codabench_bridge.py` remains only as migration context and is not part of participant
submissions.

## Production gates

- Implement the historical episode/run state model for fast Validation attempts.
- Add API-key rotation/revocation, scoped organizer credentials, audit events, and rate limits.
- Put the API behind TLS and an authenticated reverse proxy; do not publish admin endpoints.
- Migrate SQLite to PostgreSQL before using multiple API workers.
- Run publish/settle as separate idempotent scheduled jobs, not an in-process timer.
- Use the exchange calendar and record upstream market-data hashes.
- Freeze the deadline before publishing a session. Published sessions are immutable by design.
- Rehearse API/network/database restarts and settlement recovery.
- Reconcile internal teams with final Codabench code archives only if Codabench is used.
