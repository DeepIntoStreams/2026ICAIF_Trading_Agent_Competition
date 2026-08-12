"""Mock daily competition server (README #3), execution level.

Replays a run's `event_log.json` as a sequence of `decision_request` envelopes, calls a
participant agent's `decide(request) -> decision_response`, then applies the exact
server-side pipeline the live platform should use:

  structural validation  (reject malformed -> failure policy)
  -> semantic sanitize   (repair; record M9 violations)
  -> execute at close    (market-on-close)
  -> account NAV / return
  -> log

Lets a participant integrate and test their agent against the real message contract with
zero infrastructure. Run directly for a self-contained demo with the built-in starter agent:

  python scripts/mock_server.py --event-log outputs/exp2_with_news_10d/llm/event_log.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_agent.risk import sanitize_target_weights  # noqa: E402
from validate_protocol import validate_structural  # noqa: E402

PROTOCOL_VERSION = "0.1"


def build_request(session, run_id="mock_run", team_id="team_demo"):
    obs = session["observation"]
    return {
        "type": "decision_request",
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "team_id": team_id,
        "session_date": obs["session_date"],
        "deadline_utc": obs["event_time_utc"],
        "observation": obs,
    }


def process(response, allowed, constraints, prev_weights):
    """Server-side: structural -> semantic. Returns (target, violations, note)."""
    errs = validate_structural(response, "decision_response.schema.json")
    if errs or response.get("session_date") is None:
        return dict(prev_weights), ["invalid_response"], f"REJECTED {errs[:1]}"
    target, violations = sanitize_target_weights(
        response.get("target_weights", {}), allowed,
        max_asset_weight=float(constraints.get("max_asset_weight", 0.30)),
        max_gross_exposure=float(constraints.get("max_gross_exposure", 1.00)))
    return target, violations, "ok"


def run(event_log_path: str, agent=None) -> dict:
    from starter_kit_agent import StarterKitAgent  # local import for demo default
    agent = agent or StarterKitAgent()

    log = json.loads(Path(event_log_path).read_text())
    sessions = log.get("sessions", [])
    prev_weights: dict[str, float] = {}
    total_violation_days = 0
    rows = []

    for s in sessions:
        obs = s["observation"]
        allowed = [a["ticker"] for a in obs["assets"]]
        constraints = obs["constraints"]
        request = build_request(s)

        # ---- participant side ----
        response = agent.decide(request)

        # ---- server side ----
        target, violations, note = process(response, allowed, constraints, prev_weights)
        if violations:
            total_violation_days += 1
        prev_weights = target
        rows.append({
            "date": obs["session_date"],
            "n_positions": sum(1 for w in target.values() if w > 1e-6),
            "gross": round(sum(target.values()), 4),
            "violations": violations,
            "note": note,
        })

    summary = {
        "sessions": len(sessions),
        "m9_violation_rate": round(total_violation_days / max(1, len(sessions)), 4),
        "rows": rows,
    }
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event-log", required=True)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    summary = run(args.event_log)
    print(f"Sessions replayed : {summary['sessions']}")
    print(f"M9 violation rate : {summary['m9_violation_rate']}")
    if not args.quiet:
        for r in summary["rows"]:
            v = ",".join(r["violations"]) if r["violations"] else "-"
            print(f"  {r['date']}  pos={r['n_positions']:>2}  gross={r['gross']:.3f}  "
                  f"viol={v:<16} {r['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
